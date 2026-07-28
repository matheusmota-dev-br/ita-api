"""As últimas guardas: 404s, fallbacks e ramos de ambiente.

Nenhuma destas linhas roda no caminho normal. Elas existem porque o PDF pode não
estar no disco, o SQLite pode não ter FTS5, o Tesseract pode falhar num arquivo
específico e o manifest pode apontar para o que não existe. Um `except` nunca
exercitado é um `except` que pode estar errado — e falharia justamente quando já
há um problema.
"""

import json

import pymupdf
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.constants import Discipline, FormatType, TestType
from src.etl.load.decisions import (
  OP_CREATE,
  OP_RENUMBER,
  decision_for,
  load_audit,
  record_operation,
)
from src.etl.load.upserts import (
  ensure_alternatives,
  replace_question_assets,
  replace_question_pages,
  upsert_question,
)
from src.etl.transform import ocr, pages
from src.etl.transform.crop import CropError, render_crop
from src.etl.transform.text_repair import detect_repair
from src.models import Asset, Document, Exam, Question
from src.models.enums import AssetKind, DocumentKind, Phase, ReviewStatus
from src.models.question import QuestionPage
from tests.conftest import create_schema


def _pdf(tmp_path, body, name="g.pdf"):
  doc = pymupdf.open()
  page = doc.new_page(width=595, height=842)
  page.insert_textbox(pymupdf.Rect(50, 50, 545, 800), body, fontsize=11, lineheight=1.5)
  path = tmp_path / name
  doc.save(str(path))
  doc.close()
  return path


# --- classificação de página: os ramos que faltavam ------------------------


def test_draft_page_is_not_an_essay(tmp_path):
  """`RASCUNHO` vem antes de tudo: não é redação nem questão."""
  doc = pymupdf.open(_pdf(tmp_path, "RASCUNHO\n\nEspaco para calculos."))
  assert pages.is_draft_page(doc[0])
  assert not pages.is_essay_page(doc[0])
  doc.close()


def test_page_with_a_question_is_not_an_essay(tmp_path):
  doc = pymupdf.open(_pdf(tmp_path, "Questão 1. Enunciado qualquer."))
  assert not pages.is_essay_page(doc[0])
  doc.close()


def test_page_without_any_question_word_counts_as_essay(tmp_path):
  """Sem a palavra "questão" e sem ser rascunho, é texto corrido — redação."""
  doc = pymupdf.open(_pdf(tmp_path, "Texto base para a prova, sem enumeracao."))
  assert pages.is_essay_page(doc[0])
  doc.close()


def test_page_mentioning_a_question_is_not_an_essay(tmp_path):
  """A checagem procura "questao"/"questão" no singular.

  "questoes" no plural não contém a substring, então uma página que só fala em
  "questoes" é classificada como redação — vale registrar o limite.
  """
  doc = pymupdf.open(_pdf(tmp_path, "Leia a questao seguinte com atencao."))
  assert not pages.is_essay_page(doc[0])

  plural = pymupdf.open(_pdf(tmp_path, "Responda as questoes.", name="p2.pdf"))
  assert pages.is_essay_page(plural[0]), "limite conhecido do teste no singular"
  doc.close()
  plural.close()


# --- OCR: falhas do binário ------------------------------------------------


def test_availability_is_false_when_the_probe_raises(monkeypatch, tmp_path):
  """Tesseract instalado mas quebrado é indistinguível de ausente, na prática."""
  doc = pymupdf.open(_pdf(tmp_path, "x"))
  page = doc[0]

  def explode(*_args, **_kwargs):
    raise RuntimeError("tesseract sem TESSDATA_PREFIX")

  monkeypatch.setattr(type(page), "get_textpage_ocr", explode, raising=False)
  assert ocr.ocr_textpage(page) is None
  doc.close()


def test_ocr_textpage_returns_none_when_ocr_is_unavailable(monkeypatch, tmp_path):
  doc = pymupdf.open(_pdf(tmp_path, "x"))
  monkeypatch.setattr(ocr, "is_available", lambda: False)
  assert ocr.ocr_textpage(doc[0]) is None
  doc.close()


# --- recorte: retângulos impossíveis ---------------------------------------


def test_crop_of_a_page_beyond_the_document(tmp_path):
  path = _pdf(tmp_path, "Questão 1.")
  with pytest.raises(CropError, match="fora do documento"):
    render_crop(path, 99, (10, 10, 100, 100))


def test_crop_entirely_outside_the_page(tmp_path):
  path = _pdf(tmp_path, "Questão 1.")
  with pytest.raises(CropError, match="fora da pagina"):
    render_crop(path, 0, (5000, 5000, 5100, 5100))


def test_crop_of_a_missing_pdf(tmp_path):
  with pytest.raises(CropError, match="ausente"):
    render_crop(tmp_path / "nao-existe.pdf", 0, (0, 0, 10, 10))


# --- reparo de texto: saídas antecipadas -----------------------------------


def test_repair_declines_an_empty_string():
  assert detect_repair("") is None


def test_repair_declines_text_that_is_already_fine():
  clean = (
    "Considere um bloco de massa m que desliza sem atrito sobre uma "
    "superficie horizontal, submetido a uma forca constante."
  )
  assert detect_repair(clean) is None


# --- busca: fallback para LIKE --------------------------------------------


async def test_search_falls_back_to_like_without_fts5(api, monkeypatch):
  """O repositório degrada quando o SQLite não tem FTS5.

  É varredura completa e não casa acento, mas devolver resultado errado é pior
  que devolver resultado lento.
  """
  monkeypatch.setattr("src.config.settings.search_backend", "like")
  async with api as client:
    body = (await client.get("/api/v1/questions?q=complexo")).json()
    assert body["total"] >= 1


async def test_like_fallback_is_accent_sensitive(api, monkeypatch):
  """A degradação tem um custo real, e vale afirmá-lo."""
  monkeypatch.setattr("src.config.settings.search_backend", "like")
  async with api as client:
    com = (await client.get("/api/v1/questions?q=número")).json()["total"]
    sem = (await client.get("/api/v1/questions?q=numero")).json()["total"]
    assert com >= 1
    assert sem == 0, "sem FTS5, `numero` nao acha `número` — e o preco do fallback"


async def test_query_of_only_spaces_matches_nothing(api):
  """`q` só com espaços vira consulta vazia — nenhum resultado, não "tudo"."""
  async with api as client:
    assert (await client.get("/api/v1/questions?q=%20%20")).json()["total"] == 0


# --- filtros de exame ------------------------------------------------------


async def test_exam_year_range_filters(api):
  async with api as client:
    assert (await client.get("/api/v1/exams?year_min=2025")).status_code == 200
    assert (await client.get("/api/v1/exams?year_max=2020")).json() == []


# --- 404s dos endpoints de revisão ----------------------------------------


async def test_create_on_a_missing_document_is_404(api):
  async with api as client:
    response = await client.post(
      "/api/v1/documents/9999/questions",
      json={
        "number": 1,
        "bbox": {"page_no": 0, "part_index": 0, "x0": 0, "y0": 0, "x1": 100, "y1": 100},
      },
    )
    assert response.status_code == 404


async def test_delete_of_a_missing_question_is_404(api):
  async with api as client:
    assert (await client.delete("/api/v1/questions/9999")).status_code == 404


async def test_renumber_of_a_missing_question_is_404(api):
  async with api as client:
    response = await client.post("/api/v1/questions/9999/number", json={"number": 5})
    assert response.status_code == 404


async def test_recrop_when_the_pdf_is_gone_is_404(api, tmp_path):
  """O manifest aponta para um PDF que saiu do disco."""
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    source = (await client.get(f"/api/v1/questions/{qid}")).json()["source"]
    from pathlib import Path

    Path(source["local_path"]).unlink()

    response = await client.post(
      f"/api/v1/questions/{qid}/review",
      json={
        "bbox": {
          "page_no": 0,
          "part_index": 0,
          "x0": 10,
          "y0": 10,
          "x1": 500,
          "y1": 200,
        }
      },
    )
    assert response.status_code == 404
    assert "ausente" in response.json()["detail"]


async def test_page_image_when_the_pdf_is_gone_is_404(api):
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    from pathlib import Path

    Path(docs[0]["local_path"]).unlink()
    response = await client.get(f"/api/v1/documents/{docs[0]['id']}/pages/0/image")
    assert response.status_code == 404
    assert "ausente" in response.json()["detail"]


async def test_image_of_a_question_whose_file_vanished(api):
  """A linha em `assets` existe, o arquivo não."""
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    from src.config import settings

    (settings.media_root / "crops" / "q1.png").unlink()
    assert (await client.get(f"/api/v1/questions/{qid}/image")).status_code == 404


async def test_image_part_beyond_what_exists(api):
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    assert (
      await client.get(f"/api/v1/questions/{qid}/image?part=9")
    ).status_code == 404


async def test_summary_without_pages_has_no_image_url(api):
  """A questão 2 do fixture tem página mas não asset; a 3 nem página."""
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    urls = {i["number"]: i["image_url"] for i in listing["items"]}
    assert urls[1]


async def test_root_and_health(api):
  async with api as client:
    assert (await client.get("/")).json()["docs"] == "/docs"
    assert (await client.get("/health")).json()["status"] == "ok"


# --- decisões: validação e leitura ----------------------------------------


def test_unknown_operation_is_rejected(tmp_path):
  with pytest.raises(ValueError, match="operacao desconhecida"):
    record_operation(
      op="teleportar", local_path="p.pdf", number=1, path=tmp_path / "d.json"
    )


def test_renumber_without_a_target_is_rejected(tmp_path):
  with pytest.raises(ValueError, match="numero de destino"):
    record_operation(
      op=OP_RENUMBER, local_path="p.pdf", number=1, path=tmp_path / "d.json"
    )


def test_operation_records_a_note(tmp_path):
  path = tmp_path / "d.json"
  record_operation(
    op=OP_CREATE,
    local_path="p.pdf",
    number=1,
    bbox={"page_no": 0, "x0": 0, "y0": 0, "x1": 1, "y1": 1},
    note="detecção perdeu esta",
    path=path,
  )
  from src.etl.load.decisions import load_operations

  assert load_operations(path)[0]["note"] == "detecção perdeu esta"


def test_audit_of_a_missing_file_is_empty(tmp_path):
  assert load_audit(tmp_path / "nao-existe.jsonl") == []


def test_decision_for_looks_up_by_natural_key():
  decisions = {"p.pdf#3": {"status": "rejected"}}
  assert decision_for(decisions, "p.pdf", 3)["status"] == "rejected"
  assert decision_for(decisions, "p.pdf", 4) is None


def test_reader_of_a_missing_root_is_empty(tmp_path):
  from src.etl.load.reader import iter_processed

  assert iter_processed(tmp_path / "nao-existe") == []


# --- upserts: os ramos de reescrita ---------------------------------------


@pytest.fixture
def db(tmp_path):
  engine = create_engine(f"sqlite:///{tmp_path / 'u.db'}")
  create_schema(engine)
  with Session(engine) as session:
    exam = Exam(test_type=TestType.VESTIBULAR, year=2018, phase=Phase.UNICA)
    session.add(exam)
    session.flush()
    document = Document(
      exam_id=exam.id,
      kind=DocumentKind.PROVA,
      source_column="fisica",
      local_path="x.pdf",
      sha256="s",
    )
    session.add(document)
    session.flush()
    session.commit()
    yield session, exam, document


def _entry(number=1, sha="h1"):
  return {
    "number": number,
    "source_sha256": sha,
    "format_type": "objective",
    "page_start": 0,
    "page_end": 0,
    "parts": [
      {
        "part_index": 0,
        "page_no": 0,
        "bbox": [10, 20, 500, 300],
        "image": "questoes/questao_01.png",
        "image_sha256": "img",
      }
    ],
  }


def test_unchanged_question_is_not_rewritten(db):
  """O upsert só escreve quando o sha da origem muda; `updated_at` fica honesto."""
  session, exam, document = db
  first, changed = upsert_question(session, exam, document, _entry(), "enunciado")
  assert changed is True

  again, changed = upsert_question(session, exam, document, _entry(), "enunciado")
  assert changed is False
  assert again.id == first.id


def test_changed_source_rewrites(db):
  session, exam, document = db
  upsert_question(session, exam, document, _entry(sha="h1"), "a")
  _question, changed = upsert_question(session, exam, document, _entry(sha="h2"), "b")
  assert changed is True


def test_replacing_pages_removes_the_old_ones(db):
  session, exam, document = db
  question, _ = upsert_question(session, exam, document, _entry(), "a")
  replace_question_pages(session, question, _entry()["parts"])
  session.flush()
  assert len(question.pages) == 1

  # Uma segunda passada com outra geometria não pode acumular linhas.
  novo = [{**_entry()["parts"][0], "bbox": [0, 0, 100, 100]}]
  replace_question_pages(session, question, novo)
  session.flush()
  assert len(question.pages) == 1
  assert question.pages[0].x1 == 100


def test_replacing_assets_removes_the_old_ones(db, tmp_path, monkeypatch):
  session, exam, document = db
  monkeypatch.setattr("src.config.settings.media_root", tmp_path)
  question, _ = upsert_question(session, exam, document, _entry(), "a")

  replace_question_assets(session, question, _entry()["parts"], "processed/x")
  session.flush()
  assert len(question.assets) == 1

  replace_question_assets(session, question, _entry()["parts"], "processed/x")
  session.flush()
  assert len(question.assets) == 1


def test_parts_without_an_image_are_skipped(db, tmp_path, monkeypatch):
  session, exam, document = db
  monkeypatch.setattr("src.config.settings.media_root", tmp_path)
  question, _ = upsert_question(session, exam, document, _entry(), "a")

  parts = [{"part_index": 0, "page_no": 0, "bbox": [0, 0, 10, 10]}]
  replace_question_assets(session, question, parts, "processed/x")
  session.flush()
  assert question.assets == [], "parte sem imagem nao gera asset"


def test_alternatives_are_not_created_for_an_essay(db):
  """Redação não tem alternativas; criar cinco vazias seria mentira."""
  session, exam, document = db
  entry = {**_entry(), "format_type": "essay"}
  question, _ = upsert_question(session, exam, document, entry, "tema")
  ensure_alternatives(session, question)
  assert question.alternatives == []


# --- config: URLs que não são arquivo -------------------------------------


def test_sqlite_path_is_none_for_memory(monkeypatch):
  from src.config import settings

  monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///:memory:")
  assert settings.sqlite_path is None


def test_sqlite_path_is_none_for_another_dialect(monkeypatch):
  from src.config import settings

  monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://x/y")
  assert settings.sqlite_path is None


def test_document_pending_count():
  from src.schemas.document import DocumentOut

  out = DocumentOut(
    id=1,
    exam_id=1,
    exam_slug="s",
    test_type=TestType.VESTIBULAR,
    year=2025,
    phase=Phase.PRIMEIRA,
    kind=DocumentKind.PROVA,
    source_column="c",
    local_path="p",
    question_count=10,
    reviewed_count=3,
    rejected_count=2,
  )
  assert out.pending_count == 5


# --- triagem: as razões ----------------------------------------------------


def test_triage_reasons_are_specific():
  from src.etl.enrich.triage import triage

  assert triage("Questao 1. Texto limpo o suficiente.", from_ocr=True).reason == (
    "texto de OCR"
  )
  clean = triage("Questao 1. Um texto perfeitamente limpo e legivel aqui.")
  assert clean.reason == "texto limpo"
  assert not clean.needs_llm


# --- verify: diferença de ambiente ----------------------------------------


def test_verify_points_at_the_environment_when_it_changed(
  tmp_path, monkeypatch, capsys
):
  """Uma divergência acompanhada de "Tesseract mudou" não vira mistério."""
  from src.etl.verify import cmd_snapshot, cmd_verify

  db = tmp_path / "env.db"
  engine = create_engine(f"sqlite:///{db}")
  create_schema(engine)
  with Session(engine) as session:
    exam = Exam(test_type=TestType.VESTIBULAR, year=2020, phase=Phase.UNICA)
    session.add(exam)
    session.flush()
    document = Document(
      exam_id=exam.id,
      kind=DocumentKind.PROVA,
      source_column="c",
      local_path="p",
      sha256="s",
    )
    session.add(document)
    session.flush()
    session.add(
      Question(
        document_id=document.id,
        exam_id=exam.id,
        number=1,
        format_type=FormatType.OBJECTIVE,
        discipline=Discipline.FISICA,
        statement_raw="x",
        page_start=0,
        page_end=0,
        source_sha256="h",
        review_status=ReviewStatus.RAW,
      )
    )
    session.commit()
  engine.dispose()
  monkeypatch.setattr("src.config.settings.database_url", f"sqlite+aiosqlite:///{db}")

  baseline = tmp_path / "b.json"
  cmd_snapshot(baseline)

  # Muda o ambiente registrado e o dado, para forçar as duas divergências.
  payload = json.loads(baseline.read_text(encoding="utf-8"))
  payload["environment"]["tesseract"]["version"] = "tesseract 4.0.0"
  payload["counts"]["questions"] = 99
  payload["fingerprint"] = "0" * 16
  baseline.write_text(json.dumps(payload), encoding="utf-8")

  assert cmd_verify(baseline) == 1
  out = capsys.readouterr().out
  assert "DIVERGENCIA" in out
  assert "ambiente também mudou" in out
  assert "tesseract" in out


# --- split: texto antes do primeiro marcador ------------------------------


def test_text_before_the_first_marker_on_the_same_line():
  """`...pode-se afirmar que A ( ) primeira` — o head pertence ao enunciado."""
  from src.etl.transform.split import split_statement

  split = split_statement(
    "Enunciado da questao, pode-se afirmar que A ( ) primeira B ( ) segunda\n"
    "C ( ) terceira\nD ( ) quarta\nE ( ) quinta"
  )
  assert split.complete
  assert "pode-se afirmar que" in split.statement
  assert split.alternatives["A"] == "primeira"


# --- naming: pós é objetiva -----------------------------------------------


def test_pos_second_phase_is_still_objective():
  """A pós não tem 2ª fase dissertativa; o default não pode assumir que tem."""
  from src.etl.transform.naming import ExamRef

  ref = ExamRef(
    test_type=TestType.POS,
    year=2024,
    phase=Phase.POS_1SEM,
    discipline=Discipline.MATEMATICA,
    kind=DocumentKind.PROVA,
    source_column="matematica",
    format_index=0,
    path=None,
  )
  assert ref.default_format_type is FormatType.OBJECTIVE


# --- assets órfãos ---------------------------------------------------------


async def test_traversal_in_relative_path_is_refused(api, tmp_path):
  """`relative_path` vem do ETL; sem a checagem de containment seria traversal."""
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    with Session(engine) as session:
      asset = session.scalar(select(Asset).where(Asset.question_id == qid))
      asset.relative_path = "../../../etc/passwd"
      session.commit()
    engine.dispose()

    assert (await client.get(f"/api/v1/questions/{qid}/image")).status_code == 404


def test_question_page_and_asset_models_accept_a_bbox(db):
  session, exam, document = db
  question = Question(
    document_id=document.id,
    exam_id=exam.id,
    number=7,
    format_type=FormatType.OBJECTIVE,
    statement_raw="x",
    page_start=0,
    page_end=0,
    source_sha256="h7",
    review_status=ReviewStatus.RAW,
  )
  question.pages.append(QuestionPage(part_index=0, page_no=0, x0=1, y0=2, x1=3, y1=4))
  question.assets.append(
    Asset(
      kind=AssetKind.QUESTION_CROP,
      relative_path="a/b.png",
      page_no=0,
      bbox=[1, 2, 3, 4],
      sha256="s",
    )
  )
  session.add(question)
  session.flush()
  assert question.assets[0].bbox == [1, 2, 3, 4]
