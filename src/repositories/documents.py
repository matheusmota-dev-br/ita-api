"""Queries da interface de auditoria."""

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Alternative, AnswerKeyEntry, Document, Exam, Question
from src.models.enums import ReviewStatus


def _counts_subquery() -> Select:
  """Contagens por documento, calculadas em uma passada só.

  A listagem mostra 158 documentos; fazer N+1 consultas de contagem deixaria a
  interface lenta justamente na tela que abre primeiro.
  """
  answered = (
    select(Alternative.question_id)
    .where(Alternative.is_correct.is_(True))
    .distinct()
    .subquery()
  )
  return (
    select(
      Question.document_id.label("document_id"),
      func.count().label("total"),
      func.sum(
        func.iif(Question.review_status == ReviewStatus.HUMAN_VERIFIED, 1, 0)
      ).label("reviewed"),
      func.sum(func.iif(Question.review_status == ReviewStatus.REJECTED, 1, 0)).label(
        "rejected"
      ),
      func.count(answered.c.question_id).label("with_answer"),
    )
    .outerjoin(answered, answered.c.question_id == Question.id)
    .group_by(Question.document_id)
    .subquery()
  )


async def list_documents(session: AsyncSession) -> list[dict]:
  counts = _counts_subquery()
  # Gabaritos não têm questões, têm respostas. Sem esta contagem os 21
  # documentos de gabarito apareceriam como "0 questões", indistinguíveis de
  # uma prova que falhou na detecção.
  answers = (
    select(
      AnswerKeyEntry.document_id.label("document_id"),
      func.count().label("answers"),
    )
    .group_by(AnswerKeyEntry.document_id)
    .subquery()
  )
  stmt = (
    select(
      Document,
      Exam,
      counts.c.total,
      counts.c.reviewed,
      counts.c.rejected,
      counts.c.with_answer,
      answers.c.answers,
    )
    .join(Exam, Exam.id == Document.exam_id)
    .outerjoin(counts, counts.c.document_id == Document.id)
    .outerjoin(answers, answers.c.document_id == Document.id)
    .order_by(Exam.test_type, Exam.year.desc(), Exam.phase, Document.discipline)
  )

  out: list[dict] = []
  for (
    document,
    exam,
    total,
    reviewed,
    rejected,
    with_answer,
    answer_count,
  ) in await session.execute(stmt):
    out.append(
      {
        "id": document.id,
        "exam_id": exam.id,
        "exam_slug": exam.slug,
        "test_type": exam.test_type,
        "year": exam.year,
        "phase": exam.phase,
        "discipline": document.discipline,
        "kind": document.kind,
        "source_column": document.source_column,
        "local_path": document.local_path,
        "page_count": document.page_count,
        "question_count": total or 0,
        "reviewed_count": int(reviewed or 0),
        "rejected_count": int(rejected or 0),
        "with_answer_count": with_answer or 0,
        "answer_count": answer_count or 0,
      }
    )
  return out


async def get_document(session: AsyncSession, document_id: int) -> Document | None:
  return await session.scalar(select(Document).where(Document.id == document_id))


async def document_question_ids(session: AsyncSession, document_id: int) -> list[int]:
  stmt = (
    select(Question.id)
    .where(Question.document_id == document_id)
    .order_by(Question.number)
  )
  return list(await session.scalars(stmt))


async def review_progress(session: AsyncSession) -> dict[str, int]:
  total = await session.scalar(select(func.count()).select_from(Question)) or 0
  reviewed = (
    await session.scalar(
      select(func.count())
      .select_from(Question)
      .where(Question.review_status == ReviewStatus.HUMAN_VERIFIED)
    )
    or 0
  )
  rejected = (
    await session.scalar(
      select(func.count())
      .select_from(Question)
      .where(Question.review_status == ReviewStatus.REJECTED)
    )
    or 0
  )
  return {
    "total": total,
    "reviewed": reviewed,
    "rejected": rejected,
    "pending": total - reviewed - rejected,
  }


def _status_columns() -> list:
  """As três contagens de estado, para reusar em qualquer agrupamento."""
  return [
    func.count().label("total"),
    func.sum(
      func.iif(Question.review_status == ReviewStatus.HUMAN_VERIFIED, 1, 0)
    ).label("reviewed"),
    func.sum(func.iif(Question.review_status == ReviewStatus.REJECTED, 1, 0)).label(
      "rejected"
    ),
    func.sum(func.iif(Question.needs_llm.is_(True), 1, 0)).label("needs_llm"),
    func.sum(
      func.iif(
        func.length(func.trim(func.coalesce(Question.statement_raw, ""))) == 0, 1, 0
      )
    ).label("empty_text"),
  ]


def _bucket(row, label: str) -> dict:
  total, reviewed, rejected, needs_llm, empty_text = row[1:]
  return {
    "label": label,
    "total": total or 0,
    "reviewed": int(reviewed or 0),
    "rejected": int(rejected or 0),
    "pending": (total or 0) - int(reviewed or 0) - int(rejected or 0),
    "needs_llm": int(needs_llm or 0),
    "empty_text": int(empty_text or 0),
  }


async def progress_breakdown(session: AsyncSession) -> dict:
  """Progresso da auditoria fatiado por tipo, ano, fase e matéria.

  O painel existe para responder "por onde continuar": a contagem global diz
  quanto falta, mas não onde. Cada corte é uma consulta agregada, não N
  consultas por grupo, senão a página levaria segundos para abrir.
  """

  async def group_by(column, formatter=str) -> list[dict]:
    stmt = (
      select(column, *_status_columns())
      .select_from(Question)
      .join(Exam, Exam.id == Question.exam_id)
      .group_by(column)
      .order_by(column)
    )
    return [
      _bucket(row, formatter(row[0]) if row[0] is not None else "sem")
      for row in await session.execute(stmt)
    ]

  by_document_stmt = (
    select(Document.id, *_status_columns())
    .select_from(Question)
    .join(Document, Document.id == Question.document_id)
    .group_by(Document.id)
  )
  documents = {
    row[0]: _bucket(row, str(row[0])) for row in await session.execute(by_document_stmt)
  }

  return {
    "overall": await review_progress(session),
    "by_test_type": await group_by(Exam.test_type, lambda v: v.value),
    "by_year": await group_by(Exam.year),
    "by_phase": await group_by(Exam.phase, lambda v: v.value),
    "by_discipline": await group_by(Question.discipline, lambda v: v.value),
    "by_document": documents,
  }


async def document_answers(session: AsyncSession, document_id: int) -> list[dict]:
  """Respostas parseadas de um gabarito, para conferir contra o PDF."""
  stmt = (
    select(AnswerKeyEntry)
    .where(AnswerKeyEntry.document_id == document_id)
    .order_by(AnswerKeyEntry.column_index, AnswerKeyEntry.question_number)
  )
  return [
    {
      "number": entry.question_number,
      "letter": entry.correct_letter,
      "annulled": entry.annulled,
      "discipline": entry.discipline.value if entry.discipline else None,
      "column": entry.column_index,
      "raw": entry.raw_line,
    }
    for entry in await session.scalars(stmt)
  ]
