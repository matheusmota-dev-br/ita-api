"""Caminhos do load que os testes de caminho felizo não alcançam.

Aqui ficam as ramificações que só acontecem quando algo dá errado ou quando o
enrich já rodou: aplicar a saída do LLM, casar gabarito por matéria, reaplicar um
bbox corrigido, e cada modo de falha do replay de operações. São justamente os
trechos onde um erro é silencioso — o corpus fica errado e nada avisa.
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.constants import Discipline, FormatType, TestType
from src.etl.load import main as load_main
from src.etl.load.decisions import OP_CREATE, OP_DELETE, OP_RENUMBER
from src.etl.load.main import (
  LoadStats,
  _apply_answer_keys,
  _apply_decision_bbox,
  _apply_enrichment,
  _apply_review_decisions,
  _apply_topics,
  _replay_operations,
)
from src.models import (
  Alternative,
  AnswerKeyEntry,
  Document,
  Exam,
  Question,
  Topic,
)
from src.models.enums import DisciplineSource, DocumentKind, Phase, ReviewStatus
from src.models.question import QuestionPage
from tests.conftest import create_schema

ENRICHED = {
  "statement_markdown": "Seja $z$ um complexo de módulo $1$.",
  "has_figure": True,
  "detected_discipline": "matematica",
  "detected_topics": ["numeros-complexos", "Números Complexos", "  "],
  "alternatives": [
    {"letter": "A", "text_markdown": "$1$", "is_figure_only": False},
    {"letter": "B", "text_markdown": "figura", "is_figure_only": True},
    {"letter": "Z", "text_markdown": "nao existe"},
  ],
}


@pytest.fixture
def session(tmp_path):
  """Sessão síncrona com um exame, um documento e duas questões."""
  engine = create_engine(f"sqlite:///{tmp_path / 'load.db'}")
  create_schema(engine)
  with Session(engine) as db:
    exam = Exam(test_type=TestType.VESTIBULAR, year=2015, phase=Phase.UNICA)
    db.add(exam)
    db.flush()
    document = Document(
      exam_id=exam.id,
      kind=DocumentKind.PROVA,
      source_column="fisica",
      local_path="data/x.pdf",
      sha256="s",
    )
    db.add(document)
    db.flush()
    for number in (1, 2):
      question = Question(
        document_id=document.id,
        exam_id=exam.id,
        number=number,
        format_type=FormatType.OBJECTIVE,
        statement_raw=f"enunciado {number}",
        page_start=0,
        page_end=0,
        source_sha256=f"h{number}",
        review_status=ReviewStatus.RAW,
      )
      question.pages.append(
        QuestionPage(part_index=0, page_no=0, x0=10, y0=20, x1=500, y1=300)
      )
      for index, letter in enumerate("ABCDE"):
        question.alternatives.append(
          Alternative(letter=letter, order_index=index, review_status=ReviewStatus.RAW)
        )
      db.add(question)
    db.commit()
    yield db


def _question(db, number=1) -> Question:
  return db.scalar(select(Question).where(Question.number == number))


# --- enriquecimento --------------------------------------------------------


def test_enrichment_writes_markdown_without_touching_raw(session):
  """A extração heurística é a fonte auditável; o LLM só preenche o Markdown."""
  question = _question(session)
  raw_before = question.statement_raw
  stats = LoadStats()

  _apply_enrichment(session, question, ENRICHED, stats)

  assert question.statement_markdown == ENRICHED["statement_markdown"]
  assert question.statement_raw == raw_before
  assert question.review_status is ReviewStatus.LLM_GENERATED
  assert question.has_figure is True
  assert stats.enriched == 1


def test_enrichment_fills_the_discipline_only_when_absent(session):
  question = _question(session)
  stats = LoadStats()
  _apply_enrichment(session, question, ENRICHED, stats)
  assert question.discipline is Discipline.MATEMATICA
  assert question.discipline_source is DisciplineSource.LLM
  assert stats.disciplines_from_llm == 1


def test_enrichment_does_not_override_a_better_discipline_source(session):
  """Cabeçalho de seção e nome de arquivo são mais confiáveis que o LLM."""
  question = _question(session)
  question.discipline = Discipline.FISICA
  question.discipline_source = DisciplineSource.SECTION_HEADER
  stats = LoadStats()

  _apply_enrichment(session, question, ENRICHED, stats)
  assert question.discipline is Discipline.FISICA
  assert question.discipline_source is DisciplineSource.SECTION_HEADER
  assert stats.disciplines_from_llm == 0


def test_enrichment_ignores_indeterminate_discipline(session):
  question = _question(session)
  stats = LoadStats()
  _apply_enrichment(
    session, question, {**ENRICHED, "detected_discipline": "indeterminado"}, stats
  )
  assert question.discipline is None


def test_enrichment_writes_alternatives_and_ignores_unknown_letters(session):
  question = _question(session)
  _apply_enrichment(session, question, ENRICHED, LoadStats())
  by_letter = {a.letter: a for a in question.alternatives}
  assert by_letter["A"].text_markdown == "$1$"
  assert by_letter["B"].is_figure_only is True
  assert "Z" not in by_letter, "letra fora de A–E nao deve criar alternativa"


def test_enrichment_skips_a_human_verified_question(session):
  """Uma questão conferida à mão não pode ser sobrescrita pelo LLM."""
  question = _question(session)
  question.review_status = ReviewStatus.HUMAN_VERIFIED
  stats = LoadStats()

  _apply_enrichment(session, question, ENRICHED, stats)
  assert question.statement_markdown is None
  assert stats.enriched == 0


def test_enrichment_skips_a_human_verified_alternative(session):
  question = _question(session)
  target = next(a for a in question.alternatives if a.letter == "A")
  target.text_markdown = "escrito à mão"
  target.review_status = ReviewStatus.HUMAN_VERIFIED

  _apply_enrichment(session, question, ENRICHED, LoadStats())
  assert target.text_markdown == "escrito à mão"


# --- tópicos ---------------------------------------------------------------


def test_topics_are_normalised_and_deduped(session):
  """Grafias variantes têm que convergir para o mesmo slug."""
  question = _question(session)
  _apply_topics(session, question, ["Números Complexos", "numeros complexos", "  "])
  slugs = sorted(t.slug for t in question.topics)
  assert slugs == ["numeros-complexos", "números-complexos"]


def test_existing_topic_is_reused(session):
  question = _question(session)
  session.add(Topic(slug="cinematica", label="Cinematica"))
  session.flush()
  _apply_topics(session, question, ["cinematica"])
  assert (
    session.scalar(select(Topic).where(Topic.slug == "cinematica")) in question.topics
  )
  assert len(session.scalars(select(Topic)).all()) == 1


def test_topic_is_not_added_twice(session):
  question = _question(session)
  _apply_topics(session, question, ["algebra"])
  _apply_topics(session, question, ["algebra"])
  assert len(question.topics) == 1


# --- gabarito --------------------------------------------------------------


def _answer(db, number, letter, discipline=None):
  question = _question(db, number)
  db.add(
    AnswerKeyEntry(
      document_id=question.document_id,
      exam_id=question.exam_id,
      question_number=number,
      correct_letter=letter,
      discipline=discipline,
      column_index=0,
    )
  )
  db.flush()


def test_answer_key_matches_by_number(session):
  _answer(session, 1, "C")
  stats = LoadStats()
  _apply_answer_keys(session, stats)
  correct = [a.letter for a in _question(session).alternatives if a.is_correct]
  assert correct == ["C"]
  assert stats.alternatives_marked == 1


def test_answer_key_prefers_matching_the_discipline(session):
  """Em 2008–2018 cada matéria recomeça em 1; a matéria desambigua."""
  question = _question(session)
  question.discipline = Discipline.FISICA
  session.flush()
  _answer(session, 1, "D", Discipline.FISICA)

  _apply_answer_keys(session, LoadStats())
  assert [a.letter for a in question.alternatives if a.is_correct] == ["D"]


def test_answer_key_falls_back_when_the_discipline_does_not_match(session):
  """Cai para (prova, número), que basta na 1ª fase de numeração global."""
  _answer(session, 1, "E", Discipline.QUIMICA)
  _apply_answer_keys(session, LoadStats())
  assert [a.letter for a in _question(session).alternatives if a.is_correct] == ["E"]


def test_answer_key_without_a_letter_is_ignored(session):
  _answer(session, 1, None)
  stats = LoadStats()
  _apply_answer_keys(session, stats)
  assert stats.alternatives_marked == 0


def test_answer_key_conflict_with_human_review_is_reported_not_fatal(session):
  """O gabarito discordar da revisão humana derrubava o load inteiro.

  A guarda protegia a linha do humano mas não impedia marcar uma segunda
  correta, e o índice parcial `UNIQUE(question_id) WHERE is_correct = 1`
  respondia com IntegrityError. Agora a questão é pulada — a decisão humana é a
  palavra final — e o conflito é registrado em vez de escondido.
  """
  question = _question(session)
  target = next(a for a in question.alternatives if a.letter == "A")
  target.is_correct = True
  target.review_status = ReviewStatus.HUMAN_VERIFIED
  session.flush()
  _answer(session, 1, "C")

  stats = LoadStats()
  _apply_answer_keys(session, stats)
  session.commit()

  assert target.is_correct is True, "a revisao humana e a palavra final"
  assert [a.letter for a in question.alternatives if a.is_correct] == ["A"]
  assert len(stats.answer_key_conflicts) == 1
  assert "gabarito diz C" in stats.answer_key_conflicts[0]
  assert "humana diz A" in stats.answer_key_conflicts[0]


def test_answer_key_agreeing_with_human_review_is_not_a_conflict(session):
  question = _question(session)
  target = next(a for a in question.alternatives if a.letter == "C")
  target.is_correct = True
  target.review_status = ReviewStatus.HUMAN_VERIFIED
  session.flush()
  _answer(session, 1, "C")

  stats = LoadStats()
  _apply_answer_keys(session, stats)
  assert stats.answer_key_conflicts == []


# --- bbox ------------------------------------------------------------------


def test_decision_bbox_realigns_the_page_row(session):
  question = _question(session)
  _apply_decision_bbox(
    question, {"page_no": 1, "part_index": 0, "x0": 1, "y0": 2, "x1": 3, "y1": 4}
  )
  row = question.pages[0]
  assert (row.page_no, row.x0, row.y1) == (1, 1, 4)
  assert question.page_start == 1 and question.page_end == 1


def test_decision_bbox_of_a_missing_part_is_ignored(session):
  question = _question(session)
  _apply_decision_bbox(
    question, {"page_no": 0, "part_index": 9, "x0": 1, "y0": 2, "x1": 3, "y1": 4}
  )
  assert question.pages[0].x0 == 10, "nao havia parte 9; nada deve mudar"


def test_no_bbox_is_a_no_op(session):
  question = _question(session)
  _apply_decision_bbox(question, None)
  assert question.pages[0].x0 == 10


# --- replay de operações: modos de falha -----------------------------------


def _ops(monkeypatch, operations):
  monkeypatch.setattr(load_main, "load_operations", lambda: operations)


def test_replay_of_an_unknown_document_is_reported(session, monkeypatch):
  _ops(monkeypatch, [{"op": OP_DELETE, "local_path": "fantasma.pdf", "number": 1}])
  stats = LoadStats()
  _replay_operations(session, stats)
  assert len(stats.failed_operations) == 1
  assert "documento ausente" in stats.failed_operations[0]


def test_replay_of_a_delete_on_a_missing_question_is_silent(session, monkeypatch):
  """A questão já não existe: o estado desejado já vale."""
  _ops(monkeypatch, [{"op": OP_DELETE, "local_path": "data/x.pdf", "number": 99}])
  stats = LoadStats()
  _replay_operations(session, stats)
  assert stats.questions_deleted == 0
  assert stats.failed_operations == []


def test_replay_delete_removes_the_question(session, monkeypatch):
  _ops(monkeypatch, [{"op": OP_DELETE, "local_path": "data/x.pdf", "number": 2}])
  stats = LoadStats()
  _replay_operations(session, stats)
  session.commit()
  assert stats.questions_deleted == 1
  assert _question(session, 2) is None


def test_replay_create_is_skipped_when_detection_caught_up(session, monkeypatch):
  """Se o transform passou a detectar a questão, a detecção vence.

  Ela é reproduzível; o retângulo manual deixou de ser necessário.
  """
  _ops(
    monkeypatch,
    [
      {
        "op": OP_CREATE,
        "local_path": "data/x.pdf",
        "number": 1,
        "bbox": {"page_no": 0, "x0": 0, "y0": 0, "x1": 10, "y1": 10},
      }
    ],
  )
  stats = LoadStats()
  _replay_operations(session, stats)
  assert stats.questions_created == 0
  assert stats.failed_operations == []


def test_replay_create_reports_a_missing_pdf(session, monkeypatch):
  """O PDF de origem não está em disco: registra e segue, não derruba o load."""
  _ops(
    monkeypatch,
    [
      {
        "op": OP_CREATE,
        "local_path": "data/x.pdf",
        "number": 50,
        "bbox": {"page_no": 0, "x0": 0, "y0": 0, "x1": 100, "y1": 100},
      }
    ],
  )
  stats = LoadStats()
  _replay_operations(session, stats)
  assert stats.questions_created == 0
  assert len(stats.failed_operations) == 1


def test_replay_renumber_to_a_free_number(session, monkeypatch):
  _ops(
    monkeypatch,
    [{"op": OP_RENUMBER, "local_path": "data/x.pdf", "number": 1, "to": 40}],
  )
  stats = LoadStats()
  _replay_operations(session, stats)
  session.commit()
  assert stats.questions_renumbered == 1
  assert _question(session, 40) is not None


def test_replay_renumber_swaps_the_occupant(session, monkeypatch):
  _ops(
    monkeypatch,
    [{"op": OP_RENUMBER, "local_path": "data/x.pdf", "number": 1, "to": 2}],
  )
  stats = LoadStats()
  _replay_operations(session, stats)
  session.commit()
  assert stats.questions_renumbered == 1
  # As duas trocaram: quem era 1 é 2, e vice-versa.
  assert _question(session, 2).source_sha256 == "h1"
  assert _question(session, 1).source_sha256 == "h2"


def test_replay_renumber_of_a_missing_question_is_reported(session, monkeypatch):
  _ops(
    monkeypatch,
    [{"op": OP_RENUMBER, "local_path": "data/x.pdf", "number": 99, "to": 5}],
  )
  stats = LoadStats()
  _replay_operations(session, stats)
  assert any("ausente" in failure for failure in stats.failed_operations)


def test_replay_of_an_empty_list_does_nothing(session, monkeypatch):
  _ops(monkeypatch, [])
  stats = LoadStats()
  _replay_operations(session, stats)
  assert stats.failed_operations == []


def test_operations_are_applied_in_order(session, monkeypatch):
  """Renumerar 1→30 e depois 30→31 é diferente do contrário."""
  _ops(
    monkeypatch,
    [
      {"op": OP_RENUMBER, "local_path": "data/x.pdf", "number": 1, "to": 30},
      {"op": OP_RENUMBER, "local_path": "data/x.pdf", "number": 30, "to": 31},
    ],
  )
  _replay_operations(session, LoadStats())
  session.commit()
  assert _question(session, 31) is not None
  assert _question(session, 30) is None


# --- decisões por questão --------------------------------------------------


def _decisions(monkeypatch, decisions):
  monkeypatch.setattr(load_main, "load_decisions", lambda: decisions)


def test_decision_for_an_unknown_document_counts_as_orphan(session, monkeypatch):
  _decisions(
    monkeypatch,
    {"k": {"local_path": "fantasma.pdf", "number": 1, "status": "rejected"}},
  )
  stats = LoadStats()
  _apply_review_decisions(session, stats)
  assert stats.orphan_decisions == 1


def test_decision_for_an_unknown_question_counts_as_orphan(session, monkeypatch):
  _decisions(
    monkeypatch, {"k": {"local_path": "data/x.pdf", "number": 99, "status": "rejected"}}
  )
  stats = LoadStats()
  _apply_review_decisions(session, stats)
  assert stats.orphan_decisions == 1


def test_decision_applies_status_needs_llm_and_text(session, monkeypatch):
  _decisions(
    monkeypatch,
    {
      "k": {
        "local_path": "data/x.pdf",
        "number": 1,
        "status": "rejected",
        "needs_llm": False,
        "statement_markdown": "corrigido",
        "alternatives": {"a": "texto de A"},
      }
    },
  )
  stats = LoadStats()
  _apply_review_decisions(session, stats)
  session.commit()

  question = _question(session)
  # Texto corrigido à mão entra como human_verified, sobrepondo o status.
  assert question.statement_markdown == "corrigido"
  assert question.review_status is ReviewStatus.HUMAN_VERIFIED
  assert question.needs_llm is False
  by_letter = {a.letter: a for a in question.alternatives}
  assert by_letter["A"].text_markdown == "texto de A"
  assert stats.decisions_applied == 1


def test_no_decisions_is_a_no_op(session, monkeypatch):
  _decisions(monkeypatch, {})
  stats = LoadStats()
  _apply_review_decisions(session, stats)
  assert stats.decisions_applied == 0


# --- CLI -------------------------------------------------------------------


def test_main_reports_the_summary(tmp_path, monkeypatch, capsys):
  """`task load` sem corpus não pode explodir: relata o vazio."""
  db = tmp_path / "cli.db"
  engine = create_engine(f"sqlite:///{db}")
  create_schema(engine)
  engine.dispose()
  monkeypatch.setattr("src.config.settings.database_url", f"sqlite+aiosqlite:///{db}")
  monkeypatch.setattr(load_main, "iter_processed", lambda root=None: [])
  monkeypatch.setattr("sys.argv", ["load", "--quiet"])

  assert load_main.main() == 0
  assert "documentos" in capsys.readouterr().out


def test_main_reports_failed_operations(tmp_path, monkeypatch, capsys):
  db = tmp_path / "cli2.db"
  engine = create_engine(f"sqlite:///{db}")
  create_schema(engine)
  engine.dispose()
  monkeypatch.setattr("src.config.settings.database_url", f"sqlite+aiosqlite:///{db}")
  monkeypatch.setattr(load_main, "iter_processed", lambda root=None: [])
  monkeypatch.setattr(
    load_main,
    "load_operations",
    lambda: [{"op": OP_DELETE, "local_path": "fantasma.pdf", "number": 1}],
  )
  monkeypatch.setattr("sys.argv", ["load", "--quiet"])

  assert load_main.main() == 0
  # Falhar em silêncio reintroduziria a questão excluída sem explicação.
  assert "NAO aplicada" in capsys.readouterr().err
