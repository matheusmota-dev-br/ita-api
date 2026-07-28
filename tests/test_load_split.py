"""Preenchimento das alternativas a partir do texto extraído, no load."""

from src.constants import FormatType
from src.etl.load.main import _split_alternatives
from src.models import Alternative, Question
from src.models.enums import ReviewStatus

STATEMENT = """Questão 1. Quando a chave é fechada,

A ( ) apagam-se as duas lâmpadas.
B ( ) o brilho da L1 aumenta.
C ( ) o brilho da L2 aumenta.
D ( ) o brilho das duas aumenta.
E ( ) o brilho das duas permanece."""


def _question(**kwargs) -> Question:
  """Questão solta, com as cinco alternativas vazias como o load as cria."""
  question = Question(
    number=1, format_type=FormatType.OBJECTIVE, page_start=0, page_end=0, **kwargs
  )
  for index, letter in enumerate("ABCDE"):
    question.alternatives.append(
      Alternative(letter=letter, order_index=index, review_status=ReviewStatus.RAW)
    )
  return question


def test_fills_empty_alternatives():
  question = _question()
  assert _split_alternatives(question, STATEMENT) == 5
  by_letter = {a.letter: a.text_raw for a in question.alternatives}
  assert by_letter["A"] == "apagam-se as duas lâmpadas."
  assert by_letter["E"] == "o brilho das duas permanece."


def test_does_not_touch_the_audit_trail():
  """`statement_raw` é o artefato de extração e não pode ser reescrito."""
  question = _question(statement_raw=STATEMENT)
  _split_alternatives(question, STATEMENT)
  assert question.statement_raw == STATEMENT


def test_leaves_human_verified_alternatives_alone():
  question = _question()
  target = next(a for a in question.alternatives if a.letter == "A")
  target.text_raw = "corrigido à mão"
  target.review_status = ReviewStatus.HUMAN_VERIFIED

  assert _split_alternatives(question, STATEMENT) == 4
  assert target.text_raw == "corrigido à mão"


def test_does_not_overwrite_existing_text():
  """Reexecutar o load é idempotente: nada é reescrito."""
  question = _question()
  assert _split_alternatives(question, STATEMENT) == 5
  assert _split_alternatives(question, STATEMENT) == 0


def test_unparseable_text_fills_nothing():
  question = _question()
  assert _split_alternatives(question, "Disserte sobre o tema proposto.") == 0
  assert all(a.text_raw is None for a in question.alternatives)


def test_empty_statement_is_safe():
  assert _split_alternatives(_question(), "") == 0
  assert _split_alternatives(_question(), "   \n ") == 0


def test_partial_parse_fills_only_what_was_found():
  question = _question()
  assert _split_alternatives(question, "Enunciado.\n\nA ( ) so esta") == 1
  filled = [a.letter for a in question.alternatives if a.text_raw]
  assert filled == ["A"]


async def test_api_serves_alternative_text(api):
  """O ponto da mudança: alternativas sem texto tornam a API quase inútil."""
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    detail = (await client.get(f"/api/v1/questions/{listing['items'][0]['id']}")).json()
    assert [a["letter"] for a in detail["alternatives"]] == list("ABCDE")
