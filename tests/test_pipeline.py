"""Pipeline de ponta a ponta: PDF → transform → load → banco.

Os testes unitários provam que cada peça funciona isolada. Este prova que elas
se encaixam — que o manifest que o transform escreve é o que o load lê, que o
`exam_slug` casa nas duas pontas, que o gabarito encontra as questões. É onde um
erro de contrato aparece, e nenhum teste unitário pegaria.

O corpus é sintético mas o caminho é o real: PDFs de verdade com camada de
texto, `run_batch` de verdade, `run_load` de verdade, SQLite de verdade.
"""

import json

import pymupdf
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src.constants import Discipline, TestType
from src.etl.load.main import run_load
from src.etl.load.reader import iter_processed
from src.etl.transform.batch import run_batch
from src.etl.transform.naming import iter_exam_refs, parse_pdf_path
from src.models import Alternative, AnswerKeyEntry, Document, Exam, Question
from src.models.enums import DocumentKind, Phase
from tests.conftest import create_schema

# Fase única de 2008–2018: um PDF por matéria, numeração recomeçando em 1.
# O `format_index: 1` no índice é o que diz isso — o nome do arquivo não diz.
PROVA_PAGES = [
  """Questão 1. Considere o circuito da figura ao lado, com duas
lâmpadas idênticas ligadas em série a uma fonte de tensão V.
Quando a chave é fechada, pode-se afirmar que

A ( ) apagam-se as duas lâmpadas.
B ( ) o brilho da primeira aumenta.
C ( ) o brilho da segunda aumenta.
D ( ) o brilho das duas aumenta.
E ( ) o brilho das duas permanece o mesmo.

Questão 2. Um corpo de massa m desliza sem atrito por uma
rampa de altura h, partindo do repouso. A velocidade ao
chegar ao solo vale

A ( ) raiz de 2gh
B ( ) 2gh
C ( ) gh
D ( ) raiz de gh
E ( ) zero
""",
  """Questão 3. Sobre a dilatação térmica de um sólido isotrópico,
considere as afirmações e assinale a alternativa correta.

A ( ) o coeficiente linear é o dobro do superficial.
B ( ) o coeficiente superficial é o dobro do linear.
C ( ) o coeficiente volumétrico iguala o linear.
D ( ) não há relação entre eles.
E ( ) todos são iguais entre si.
""",
]

GABARITO_PAGE = """GABARITO

1 E
2 A
3 B
"""


def _write_pdf(path, pages: list[str]) -> None:
  """PDF com camada de texto real, uma página por item.

  `insert_textbox` em vez de `insert_text` porque o detector trabalha com as
  coordenadas de cada linha, e `insert_text` põe tudo numa linha só.
  """
  doc = pymupdf.open()
  for body in pages:
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(
      pymupdf.Rect(56, 56, 540, 800), body, fontsize=10, lineheight=1.4
    )
  doc.save(str(path))
  doc.close()


@pytest.fixture
def corpus(tmp_path):
  """Um diretório de provas com o `_index.json` que o extract escreveria."""
  provas = tmp_path / "vestibular" / "provas"
  provas.mkdir(parents=True)

  prova = provas / "prova_ita_vestibular_2010_fisica.pdf"
  gabarito = provas / "prova_ita_vestibular_2010_answer_key.pdf"
  _write_pdf(prova, PROVA_PAGES)
  _write_pdf(gabarito, [GABARITO_PAGE])

  (provas / "_index.json").write_text(
    json.dumps(
      {
        prova.name: {
          "filename": prova.name,
          "column": "fisica",
          "format_index": 1,
          "url": "https://exemplo/fisica.pdf",
          "sha256": "a" * 64,
        },
        gabarito.name: {
          "filename": gabarito.name,
          "column": "answer_key",
          "format_index": 1,
          "url": "https://exemplo/gab.pdf",
          "sha256": "b" * 64,
        },
      }
    ),
    encoding="utf-8",
  )
  return provas


@pytest.fixture
def loaded(corpus, tmp_path, monkeypatch, review_paths):
  """Roda transform + load de verdade e devolve (report, stats, engine)."""
  processed = tmp_path / "processed"
  media = tmp_path / "media"
  media.mkdir()
  db = tmp_path / "pipeline.db"

  monkeypatch.setattr("src.config.settings.media_root", media)
  # `database_url_sync` é propriedade derivada: quem se aponta é a de origem.
  monkeypatch.setattr("src.config.settings.database_url", f"sqlite+aiosqlite:///{db}")

  report = run_batch(provas_dirs=(corpus,), out_root=processed, verbose=False)

  # O load espera o schema pronto; em produção é o `alembic upgrade`.
  engine = create_engine(f"sqlite:///{db}")
  create_schema(engine)
  engine.dispose()

  stats = run_load(processed_root=processed, verbose=False)
  return report, stats, create_engine(f"sqlite:///{db}")


# --- transform -------------------------------------------------------------


def test_index_disambiguates_the_era(corpus):
  """`fisica` com format_index 1 é fase única, não 2ª fase.

  É o bug mais caro possível aqui: produziria um corpus errado com aparência de
  certo, porque as três eras do site usam as mesmas colunas.
  """
  ref = parse_pdf_path(corpus / "prova_ita_vestibular_2010_fisica.pdf")
  assert ref.phase is Phase.UNICA
  assert ref.discipline is Discipline.FISICA
  assert ref.kind is DocumentKind.PROVA
  assert ref.test_type is TestType.VESTIBULAR
  assert ref.slug == "vestibular-2010-unica-fisica"


def test_answer_key_is_recognised_as_such(corpus):
  ref = parse_pdf_path(corpus / "prova_ita_vestibular_2010_answer_key.pdf")
  assert ref.kind is DocumentKind.GABARITO


def test_unknown_filename_is_skipped_not_fatal(corpus):
  """Um arquivo inesperado no diretório não pode derrubar o lote."""
  (corpus / "leia-me.pdf").write_bytes(b"%PDF-1.4\n")
  assert parse_pdf_path(corpus / "leia-me.pdf") is None
  assert len(iter_exam_refs(corpus)) == 2


def test_transform_detects_every_question(loaded):
  report, _, _ = loaded
  assert report.by_status().get("empty", 0) == 0, "nenhum documento pode ficar vazio"

  # `total_questions` soma **todos** os documentos, e as respostas de um gabarito
  # entram na conta: 3 questões da prova + 3 respostas. É o mesmo motivo pelo
  # qual o corpus real reporta 4349 e tem 2730 questões — vale afirmar aqui para
  # que ninguém leia o número do relatório como "quantas questões existem".
  assert report.total_questions == 6
  by_slug = {d.slug: d.questions for d in report.documents}
  assert by_slug["vestibular-2010-unica-fisica"] == 3
  assert by_slug["vestibular-2010-unica-gabarito"] == 3


def test_manifest_is_readable_by_the_load(loaded):
  """O contrato entre os dois estágios: o que um escreve o outro tem que ler."""
  _, _, engine = loaded
  engine.dispose()


def test_crops_are_written_to_disk(loaded, tmp_path):
  pngs = list((tmp_path / "processed").rglob("questao_*.png"))
  assert len(pngs) >= 3
  assert all(p.stat().st_size > 0 for p in pngs)


# --- load ------------------------------------------------------------------


def test_load_populates_the_schema(loaded):
  _, stats, engine = loaded
  with Session(engine) as session:
    assert session.scalar(select(func.count()).select_from(Exam)) == 1
    # Prova + gabarito são dois documentos do mesmo exame.
    assert session.scalar(select(func.count()).select_from(Document)) == 2
    assert session.scalar(select(func.count()).select_from(Question)) == 3
  assert stats.questions == 3
  engine.dispose()


def test_alternatives_get_text_from_the_pdf(loaded):
  """Sem isto a API serve cinco opções em branco."""
  _, stats, engine = loaded
  with Session(engine) as session:
    filled = session.scalar(
      select(func.count())
      .select_from(Alternative)
      .where(func.length(func.trim(func.coalesce(Alternative.text_raw, ""))) > 0)
    )
    first = session.scalar(select(Question).where(Question.number == 1))
    texts = {a.letter: a.text_raw for a in first.alternatives}
  assert filled >= 10, f"esperava as alternativas das 3 questoes, veio {filled}"
  assert texts["A"] == "apagam-se as duas lâmpadas."
  assert stats.alternatives_from_text >= 10
  engine.dispose()


def test_answer_key_is_projected_onto_alternatives(loaded):
  """O gabarito diz 1→E, 2→A, 3→B; a projeção tem que casar por número."""
  _, _, engine = loaded
  with Session(engine) as session:
    assert session.scalar(select(func.count()).select_from(AnswerKeyEntry)) == 3
    correct = {
      question.number: next(
        (a.letter for a in question.alternatives if a.is_correct), None
      )
      for question in session.scalars(select(Question))
    }
  assert correct == {1: "E", 2: "A", 3: "B"}
  engine.dispose()


def test_search_blob_includes_alternative_text(loaded):
  """A busca precisa achar termo que só existe dentro de uma alternativa."""
  _, _, engine = loaded
  with Session(engine) as session:
    blobs = list(session.scalars(select(Question.search_blob)))
  assert any("rampa" in (b or "") for b in blobs)
  assert any("permanece o mesmo" in (b or "") for b in blobs)
  engine.dispose()


def test_reload_is_idempotent(loaded, tmp_path):
  """Rodar duas vezes não duplica nem reescreve."""
  _, first, engine = loaded
  engine.dispose()
  second = run_load(processed_root=tmp_path / "processed", verbose=False)
  assert second.questions == first.questions
  assert second.questions_updated == 0, "nada mudou, nada deve ser reescrito"
  assert second.alternatives_from_text == 0, "o texto já estava lá"


def test_reader_finds_both_documents(loaded, tmp_path):
  documents = iter_processed(tmp_path / "processed")
  assert len(documents) == 2
  kinds = {
    d.slug: (d.manifest is not None, d.answer_key is not None) for d in documents
  }
  assert kinds["vestibular-2010-unica-fisica"] == (True, False)
  assert kinds["vestibular-2010-unica-gabarito"] == (False, True)
