"""Upserts idempotentes por chave natural.

Rodar o ETL duas vezes não pode duplicar linha nem mexer em `updated_at` do que
não mudou. Cada função aqui insere ou atualiza usando a chave natural declarada
no modelo, e o `where` do ON CONFLICT evita escrever quando nada mudou.
"""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import settings
from src.constants import Discipline, FormatType, TestType
from src.etl.transform.constants import DPI
from src.models import Alternative, AnswerKeyEntry, Asset, Document, Exam, Question
from src.models.enums import (
  AssetKind,
  DisciplineSource,
  DocumentKind,
  Phase,
  ReviewStatus,
)
from src.models.question import QuestionPage

OPTION_LETTERS = ("A", "B", "C", "D", "E")

# Assinatura e cabeçalho IHDR de um PNG: largura e altura são dois inteiros
# big-endian nos bytes 16..24. Ler só isso evita decodificar 2730 imagens
# inteiras a cada load.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def png_size(path: Path) -> tuple[int, int] | None:
  try:
    with path.open("rb") as handle:
      header = handle.read(24)
  except OSError:
    return None
  if len(header) < 24 or not header.startswith(_PNG_MAGIC):
    return None
  return (
    int.from_bytes(header[16:20], "big"),
    int.from_bytes(header[20:24], "big"),
  )


def _enum_or_none(enum_cls, value):
  return enum_cls(value) if value else None


def upsert_exam(session: Session, payload: dict) -> Exam:
  test_type = TestType(payload["test_type"])
  phase = Phase(payload["phase"])
  year = payload["year"]

  exam = session.scalars(
    select(Exam).where(
      Exam.test_type == test_type, Exam.year == year, Exam.phase == phase
    )
  ).one_or_none()
  if exam is None:
    exam = Exam(test_type=test_type, year=year, phase=phase)
    session.add(exam)
    session.flush()
  return exam


def upsert_document(session: Session, exam: Exam, payload: dict) -> Document:
  kind = DocumentKind(payload["kind"]) if "kind" in payload else DocumentKind.GABARITO
  discipline = _enum_or_none(Discipline, payload.get("discipline"))
  source_column = payload["source_column"]
  doc_info = payload["document"]

  document = session.scalars(
    select(Document).where(
      Document.exam_id == exam.id,
      Document.kind == kind,
      Document.discipline == discipline,
      Document.source_column == source_column,
    )
  ).one_or_none()

  if document is None:
    document = Document(
      exam_id=exam.id,
      kind=kind,
      discipline=discipline,
      source_column=source_column,
    )
    session.add(document)

  document.source_url = doc_info.get("source_url")
  document.local_path = doc_info["local_path"]
  document.sha256 = doc_info["sha256"]
  document.page_count = doc_info.get("page_count")
  document.processed_dir = payload.get("processed_dir")
  session.flush()
  return document


def upsert_question(
  session: Session,
  exam: Exam,
  document: Document,
  entry: dict,
  statement_raw: str,
) -> tuple[Question, bool]:
  """Insere ou atualiza uma questão. Devolve (questão, mudou)."""
  question = session.scalars(
    select(Question).where(
      Question.document_id == document.id, Question.number == entry["number"]
    )
  ).one_or_none()

  unchanged = question is not None and question.source_sha256 == entry["source_sha256"]
  if unchanged:
    return question, False

  if question is None:
    question = Question(document_id=document.id, number=entry["number"])
    session.add(question)

  # Nunca sobrescreve o que um humano revisou.
  if question.review_status is ReviewStatus.HUMAN_VERIFIED:
    return question, False

  question.exam_id = exam.id
  question.format_type = FormatType(entry["format_type"])
  question.statement_raw = statement_raw
  question.page_start = entry["page_start"]
  question.page_end = entry["page_end"]
  question.source_sha256 = entry["source_sha256"]
  question.discipline = _enum_or_none(Discipline, entry.get("discipline"))
  question.discipline_source = _enum_or_none(
    DisciplineSource, entry.get("discipline_source")
  )
  question.discipline_confidence = entry.get("discipline_confidence")
  if question.review_status is None:
    question.review_status = ReviewStatus.RAW
  session.flush()
  return question, True


def replace_question_pages(
  session: Session, question: Question, parts: list[dict]
) -> None:
  """Substitui os pedaços de uma questão. Só é chamado quando o conteúdo mudou."""
  for page in list(question.pages):
    session.delete(page)
  session.flush()

  for part in parts:
    bbox = part["bbox"]
    session.add(
      QuestionPage(
        question_id=question.id,
        part_index=part["part_index"],
        page_no=part["page_no"],
        x0=bbox[0],
        y0=bbox[1],
        x1=bbox[2],
        y1=bbox[3],
        image_path=part.get("image"),
      )
    )
  session.flush()


def replace_question_assets(
  session: Session,
  question: Question,
  parts: list[dict],
  media_prefix: str,
) -> None:
  for asset in list(question.assets):
    session.delete(asset)
  session.flush()

  for part in parts:
    if not part.get("image"):
      continue
    bbox = part["bbox"]
    relative = f"{media_prefix}/{part['image']}"
    size = png_size(settings.media_root / relative)
    session.add(
      Asset(
        kind=AssetKind.QUESTION_CROP,
        question_id=question.id,
        relative_path=relative,
        page_no=part["page_no"],
        bbox=bbox,
        dpi=DPI,
        width_px=size[0] if size else None,
        height_px=size[1] if size else None,
        sha256=part.get("image_sha256") or "",
      )
    )
  session.flush()


def ensure_alternatives(session: Session, question: Question) -> None:
  """Garante as cinco alternativas A–E de uma questão objetiva.

  O texto vem depois, do estágio de enriquecimento; aqui elas existem para que
  o gabarito tenha onde marcar a correta.
  """
  if question.format_type is not FormatType.OBJECTIVE:
    return
  existing = {alt.letter for alt in question.alternatives}
  for index, letter in enumerate(OPTION_LETTERS):
    if letter in existing:
      continue
    # Anexa pela relação em vez de `session.add` com o `question_id` cru: um
    # add direto insere a linha mas deixa `question.alternatives` desatualizada,
    # e quem inspecionasse a coleção logo depois a veria vazia.
    question.alternatives.append(
      Alternative(letter=letter, order_index=index, review_status=ReviewStatus.RAW)
    )
  session.flush()


def upsert_answer_key_entry(
  session: Session, exam: Exam, document: Document, answer: dict
) -> AnswerKeyEntry:
  discipline = _enum_or_none(Discipline, answer.get("discipline"))
  column_index = answer.get("column", 0)
  entry = session.scalars(
    select(AnswerKeyEntry).where(
      AnswerKeyEntry.document_id == document.id,
      AnswerKeyEntry.question_number == answer["number"],
      AnswerKeyEntry.discipline == discipline,
      AnswerKeyEntry.column_index == column_index,
    )
  ).one_or_none()

  if entry is None:
    entry = AnswerKeyEntry(
      document_id=document.id,
      question_number=answer["number"],
      discipline=discipline,
      column_index=column_index,
    )
    session.add(entry)

  entry.exam_id = exam.id
  entry.correct_letter = answer.get("letter")
  entry.annulled = bool(answer.get("annulled"))
  entry.raw_line = answer.get("raw")
  session.flush()
  return entry
