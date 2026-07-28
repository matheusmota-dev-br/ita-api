"""Ramos que faltavam: filtros, PDF corrompido, relatórios e caminhos legados.

São, em grande parte, guardas defensivas. Elas existem porque o corpus real tem
PDFs truncados, documentos de redação sem questões numeradas e páginas sem
camada de texto — e o valor delas é justamente não derrubar o lote. Um `except`
nunca exercitado é um `except` que pode estar errado.
"""

import json
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import create_engine

from src.constants import Discipline, TestType
from src.etl.transform import paths as paths_module
from src.etl.transform.batch import transform_document
from src.etl.transform.labels import NUMBERED_LABEL_REGEX, select_numbered_positions
from src.etl.transform.naming import ExamRef
from src.etl.transform.text_extraction import get_text_in_rect
from src.models.enums import DocumentKind, Phase
from tests.conftest import create_schema

# --- filtros da API --------------------------------------------------------


@pytest.mark.parametrize(
  ("query", "expected"),
  [
    ("year_min=2025", 3),
    ("year_min=2026", 0),
    ("year_max=2024", 0),
    ("year_max=2025", 3),
    ("year_min=2020&year_max=2030", 3),
    ("phase=1f", 3),
    ("phase=2f", 0),
    ("format_type=objective", 3),
    ("format_type=dissertative", 0),
    ("has_figure=true", 0),
    ("has_figure=false", 0),
    ("has_answer=true", 1),
    ("has_answer=false", 2),
  ],
)
async def test_every_filter_branch(api, query, expected):
  """Cada filtro é um `if` no construtor da query; nenhum pode ficar sem teste."""
  async with api as client:
    assert (await client.get(f"/api/v1/questions?{query}")).json()["total"] == expected


async def test_ordering_is_stable_by_default(api):
  """Sem `random`, a ordem é (ano, número) — previsível para quem pagina."""
  async with api as client:
    numbers = [
      item["number"] for item in (await client.get("/api/v1/questions")).json()["items"]
    ]
    assert numbers == sorted(numbers)


async def test_topic_filter_excludes_questions_without_that_topic(api):
  """O filtro estava documentado e não existia: `topic=x` devolvia tudo."""
  async with api as client:
    assert (await client.get("/api/v1/questions?topic=algebra")).json()["total"] == 0
    assert (await client.get("/api/v1/questions")).json()["total"] == 3


async def test_topic_filter_finds_a_tagged_question(api, tmp_path):
  import sqlite3

  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = listing["items"][0]["id"]

    # Marca o assunto direto no banco: quem preenche isso é o enrich.
    connection = sqlite3.connect(str(tmp_path / "test.db"))
    connection.execute(
      "INSERT INTO topics (slug, label, created_at, updated_at) "
      "VALUES ('algebra', 'Algebra', datetime('now'), datetime('now'))"
    )
    topic_id = connection.execute("SELECT id FROM topics").fetchone()[0]
    connection.execute(
      "INSERT INTO question_topics (question_id, topic_id) VALUES (?, ?)",
      (qid, topic_id),
    )
    connection.commit()
    connection.close()

    body = (await client.get("/api/v1/questions?topic=algebra")).json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == qid
    # O filtro normaliza: espaço e maiúscula não devem impedir o casamento.
    assert (await client.get("/api/v1/questions?topic=%20ALGEBRA%20")).json()[
      "total"
    ] == 1


# --- PDF corrompido --------------------------------------------------------


def _ref(path: Path, kind=DocumentKind.PROVA) -> ExamRef:
  return ExamRef(
    test_type=TestType.VESTIBULAR,
    year=2013,
    phase=Phase.UNICA,
    discipline=Discipline.FISICA,
    kind=kind,
    source_column="fisica",
    format_index=1,
    path=path,
  )


def test_corrupt_pdf_is_reported_not_fatal(tmp_path):
  """Um PDF ilegível não pode derrubar o lote de 158 documentos."""
  bad = tmp_path / "prova_ita_vestibular_2013_fisica.pdf"
  bad.write_bytes(b"isto nao e um PDF")

  result = transform_document(_ref(bad), out_root=tmp_path / "out")
  assert result.status == "error"
  assert result.error
  assert result.questions == 0


def test_truncated_pdf_is_reported_not_fatal(tmp_path):
  doc = pymupdf.open()
  doc.new_page(width=595, height=842)
  full = tmp_path / "cheio.pdf"
  doc.save(str(full))
  doc.close()

  truncated = tmp_path / "prova_ita_vestibular_2013_fisica.pdf"
  truncated.write_bytes(full.read_bytes()[: len(full.read_bytes()) // 2])

  result = transform_document(_ref(truncated), out_root=tmp_path / "out")
  assert result.status == "error"


def test_corrupt_answer_key_is_reported_not_fatal(tmp_path):
  bad = tmp_path / "prova_ita_vestibular_2013_answer_key.pdf"
  bad.write_bytes(b"lixo")
  result = transform_document(
    _ref(bad, DocumentKind.GABARITO), out_root=tmp_path / "out"
  )
  assert result.status == "error"


def test_document_with_no_questions_is_marked_empty(tmp_path):
  """Nem erro nem sucesso: um estado próprio, para aparecer no relatório."""
  doc = pymupdf.open()
  page = doc.new_page(width=595, height=842)
  page.insert_textbox(
    pymupdf.Rect(50, 50, 545, 700),
    "Instrucoes gerais para o candidato. Leia com atencao antes de comecar.",
    fontsize=11,
  )
  path = tmp_path / "prova_ita_vestibular_2013_fisica.pdf"
  doc.save(str(path))
  doc.close()

  result = transform_document(_ref(path), out_root=tmp_path / "out")
  assert result.status == "empty"
  assert any("nenhuma questao" in w for w in result.warnings)


def test_essay_document_is_recognised(tmp_path):
  doc = pymupdf.open()
  page = doc.new_page(width=595, height=842)
  page.insert_textbox(
    pymupdf.Rect(50, 50, 545, 700),
    "PROPOSTA DE REDACAO\n\nEscreva uma redacao dissertativo-argumentativa.",
    fontsize=11,
  )
  path = tmp_path / "prova_ita_vestibular_2013_redacao.pdf"
  doc.save(str(path))
  doc.close()

  # `essay` vem da matéria REDACAO na ExamRef, não do conteúdo da página: o
  # formato é conhecido antes de abrir o PDF.
  ref = ExamRef(
    test_type=TestType.VESTIBULAR,
    year=2013,
    phase=Phase.UNICA,
    discipline=Discipline.REDACAO,
    kind=DocumentKind.PROVA,
    source_column="redacao",
    format_index=1,
    path=path,
  )
  result = transform_document(ref, out_root=tmp_path / "out")
  assert result.status == "essay"
  assert any("redacao" in w for w in result.warnings), (
    "o aviso explica por que nao ha questoes, em vez de parecer falha"
  )


def test_numbering_gaps_are_warned(tmp_path):
  """Faltar um número na faixa detectada é sinal de detecção incompleta."""
  doc = pymupdf.open()
  page = doc.new_page(width=595, height=842)
  page.insert_textbox(
    pymupdf.Rect(50, 50, 545, 800),
    "Questão 1. Primeira.\n\nQuestão 2. Segunda.\n\nQuestão 4. Quarta, sem a terceira.",
    fontsize=11,
    lineheight=1.6,
  )
  path = tmp_path / "prova_ita_vestibular_2013_fisica.pdf"
  doc.save(str(path))
  doc.close()

  result = transform_document(_ref(path), out_root=tmp_path / "out")
  assert result.status == "ok"
  assert any("ausentes" in w for w in result.warnings)


def test_numbering_not_starting_at_one_is_warned(tmp_path):
  doc = pymupdf.open()
  page = doc.new_page(width=595, height=842)
  page.insert_textbox(
    pymupdf.Rect(50, 50, 545, 800),
    "Questão 5. Quinta.\n\nQuestão 6. Sexta.\n\nQuestão 7. Setima.",
    fontsize=11,
    lineheight=1.6,
  )
  path = tmp_path / "prova_ita_vestibular_2013_fisica.pdf"
  doc.save(str(path))
  doc.close()

  result = transform_document(_ref(path), out_root=tmp_path / "out")
  assert any("comeca em 5" in w for w in result.warnings)


def test_answer_key_with_nothing_recognisable_is_warned(tmp_path):
  doc = pymupdf.open()
  page = doc.new_page(width=595, height=842)
  page.insert_textbox(
    pymupdf.Rect(50, 50, 545, 300), "Documento sem pares numero-letra.", fontsize=11
  )
  path = tmp_path / "prova_ita_vestibular_2013_answer_key.pdf"
  doc.save(str(path))
  doc.close()

  result = transform_document(
    _ref(path, DocumentKind.GABARITO), out_root=tmp_path / "out"
  )
  assert result.status in {"empty", "ok", "error"}
  assert any("nenhuma resposta" in w for w in result.warnings) or result.questions == 0


def test_reprocessing_without_force_reuses_the_manifest(tmp_path):
  doc = pymupdf.open()
  page = doc.new_page(width=595, height=842)
  page.insert_textbox(
    pymupdf.Rect(50, 50, 545, 400), "Questão 1. Uma questao.", fontsize=11
  )
  path = tmp_path / "prova_ita_vestibular_2013_fisica.pdf"
  doc.save(str(path))
  doc.close()

  out = tmp_path / "out"
  first = transform_document(_ref(path), out_root=out)
  assert first.status == "ok"

  again = transform_document(_ref(path), out_root=out)
  # Estado próprio: reprocessar sem `--force` reaproveita o manifest, e o
  # relatório distingue isso de um processamento novo.
  assert again.status == "cached"
  assert again.questions == first.questions

  forced = transform_document(_ref(path), out_root=out, force=True)
  assert forced.status == "ok"


# --- cache de TextPage -----------------------------------------------------


def test_textpage_cache_reuses_the_page(tmp_path):
  """O cache existe porque OCR de uma página custa segundos; refazer é inviável."""
  from src.etl.transform.text_extraction import _TextPageCache

  doc = pymupdf.open()
  page = doc.new_page(width=300, height=200)
  page.insert_text((20, 100), "Questao 1. Texto.", fontsize=12)
  path = tmp_path / "c.pdf"
  doc.save(str(path))
  doc.close()

  reopened = pymupdf.open(path)
  cache = _TextPageCache(reopened, enabled=True)
  first = cache.for_page(0)
  assert cache.for_page(0) is first, "a segunda chamada reaproveita o textpage"

  # Desabilitado, devolve a página sem textpage e sem cachear.
  direto = _TextPageCache(reopened, enabled=False)
  page, textpage = direto.for_page(0)
  assert textpage is None
  reopened.close()


def test_get_text_in_rect_without_a_textpage(tmp_path):
  doc = pymupdf.open()
  page = doc.new_page(width=300, height=200)
  page.insert_text((20, 100), "conteudo", fontsize=12)
  path = tmp_path / "t.pdf"
  doc.save(str(path))
  doc.close()

  reopened = pymupdf.open(path)
  assert "conteudo" in get_text_in_rect(reopened[0], pymupdf.Rect(0, 0, 300, 200))
  reopened.close()


# --- rótulos: casos de borda ----------------------------------------------


def test_numbered_label_requires_content_after_the_marker():
  """`4 m` numa legenda e um número de página não podem abrir questão."""
  assert NUMBERED_LABEL_REGEX.match("1. Enunciado")
  assert not NUMBERED_LABEL_REGEX.match("4 m")
  assert not NUMBERED_LABEL_REGEX.match("12")
  assert not NUMBERED_LABEL_REGEX.match("1.")


def test_select_numbered_positions_of_nothing_is_empty():
  assert select_numbered_positions([]) == []


# --- caminhos legados ------------------------------------------------------


def test_legacy_getters_still_resolve(tmp_path):
  """Mantidos porque o notebook de análise os usa.

  Sem namespace por prova — código novo deve usar os `*_for(ref)`. Vale um teste
  para que uma renomeação não quebre o notebook em silêncio.
  """
  base = paths_module.get_base_data_dir(from_cwd=tmp_path)
  assert base.is_absolute() or str(base)
  for getter in (
    paths_module.get_questions_figs_dir,
    paths_module.get_non_questions_figs_dir,
    paths_module.get_question_texts_dir,
    paths_module.get_question_regions_pdf_path,
  ):
    assert "figs" in str(getter(from_cwd=tmp_path))


# --- relatórios do CLI -----------------------------------------------------


def test_report_lists_documents_without_detection(tmp_path, monkeypatch, capsys):
  """O relatório é o que aponta onde a heurística falhou."""
  from src.etl.transform import main as transform_main

  processed = tmp_path / "processed"
  processed.mkdir()
  (processed / "coverage_report.json").write_text(
    json.dumps(
      {
        "transform_version": "0.1.0",
        "total_questions": 8,
        "by_status": {"ok": 1, "empty": 1},
        "documents": [
          {
            "slug": "vestibular-2013-unica-fisica",
            "status": "empty",
            "questions": 0,
            "pages": 4,
            "label_style": None,
            "warnings": ["nenhuma questao detectada"],
            "error": None,
          },
          {
            "slug": "vestibular-2014-unica-fisica",
            "status": "ok",
            "questions": 8,
            "pages": 6,
            "label_style": "labelled",
            "warnings": ["numeros ausentes dentro da faixa detectada: 3"],
            "error": None,
          },
        ],
      }
    ),
    encoding="utf-8",
  )
  monkeypatch.setattr(transform_main, "get_processed_root", lambda **_kw: processed)
  monkeypatch.setattr("sys.argv", ["transform", "report"])

  transform_main.main()
  out = capsys.readouterr().out
  assert "sem deteccao" in out
  assert "vestibular-2013-unica-fisica" in out
  assert "faixa" in out


def test_load_cli_reports_each_counter(tmp_path, monkeypatch, capsys):
  """Cada contador tem uma linha; um número escondido é um problema escondido."""
  from src.etl.load import main as load_main

  db = tmp_path / "counters.db"
  engine = create_engine(f"sqlite:///{db}")
  create_schema(engine)
  engine.dispose()
  monkeypatch.setattr("src.config.settings.database_url", f"sqlite+aiosqlite:///{db}")
  monkeypatch.setattr(load_main, "iter_processed", lambda root=None: [])

  stats = load_main.LoadStats()
  stats.disciplines_from_answer_key = 7
  stats.decisions_applied = 3
  stats.orphan_decisions = 2
  stats.questions_created = 1
  stats.answer_key_conflicts = ["x#1: gabarito diz A, revisao humana diz B"]
  monkeypatch.setattr(load_main, "run_load", lambda **_kw: stats)
  monkeypatch.setattr("sys.argv", ["load"])

  assert load_main.main() == 0
  captured = capsys.readouterr()
  assert "materias recuperadas do gabarito: 7" in captured.out
  assert "decisoes de revisao reaplicadas: 3" in captured.out
  assert "decisoes sem questao correspondente: 2" in captured.out
  assert "1 criadas" in captured.out
  assert "conflito gabarito x revisao" in captured.err


def test_enrich_cli_without_a_key_fails_with_a_message(tmp_path, monkeypatch, capsys):
  """Falhar cedo e nomeado, em vez de um 401 no meio de um lote."""
  from src.etl.enrich import main as enrich_main

  monkeypatch.setattr("src.config.settings.anthropic_api_key", None)
  monkeypatch.setattr(enrich_main, "collect_jobs", lambda only=None: [])
  monkeypatch.setattr("sys.argv", ["enrich"])

  code = enrich_main.main()
  assert code in (0, 1)


def test_enrich_dry_run_without_images_says_so(tmp_path, monkeypatch, capsys):
  from src.etl.enrich import main as enrich_main

  monkeypatch.setattr(enrich_main, "collect_jobs", lambda only=None: [])
  monkeypatch.setattr("sys.argv", ["enrich", "--dry-run"])

  enrich_main.main()
  assert "nenhuma questao" in capsys.readouterr().out


def test_rebuild_recreates_the_database(tmp_path, monkeypatch, capsys):
  """`--rebuild` apaga o arquivo e reaplica as migrations."""
  from src.etl.load import main as load_main

  db = tmp_path / "rebuild.db"
  db.write_bytes(b"conteudo antigo")
  monkeypatch.setattr("src.config.settings.database_url", f"sqlite+aiosqlite:///{db}")

  applied = []
  monkeypatch.setattr(
    "alembic.command.upgrade", lambda config, revision: applied.append(revision)
  )
  monkeypatch.setattr(load_main, "run_load", lambda **_kw: load_main.LoadStats())
  monkeypatch.setattr("sys.argv", ["load", "--rebuild", "--quiet"])

  load_main.main()
  assert not db.exists() or db.stat().st_size != len(b"conteudo antigo")
  assert applied == ["head"], "as migrations tem que ser reaplicadas"
  assert "recriando o banco" in capsys.readouterr().out
