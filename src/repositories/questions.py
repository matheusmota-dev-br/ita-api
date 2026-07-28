"""Queries de questões."""

from dataclasses import dataclass, field

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.constants import Discipline, FormatType, TestType
from src.models import Alternative, Document, Exam, Question
from src.models.enums import Phase, ReviewStatus
from src.repositories.search import search_question_ids


@dataclass
class QuestionFilters:
  q: str | None = None
  year: int | None = None
  year_min: int | None = None
  year_max: int | None = None
  test_type: TestType | None = None
  phase: Phase | None = None
  discipline: list[Discipline] = field(default_factory=list)
  format_type: FormatType | None = None
  has_figure: bool | None = None
  has_answer: bool | None = None
  review_status: list[ReviewStatus] = field(default_factory=list)
  order: str = "year_desc"


def _apply_filters(stmt: Select, filters: QuestionFilters) -> Select:
  stmt = stmt.join(Exam, Exam.id == Question.exam_id)
  if filters.year is not None:
    stmt = stmt.where(Exam.year == filters.year)
  if filters.year_min is not None:
    stmt = stmt.where(Exam.year >= filters.year_min)
  if filters.year_max is not None:
    stmt = stmt.where(Exam.year <= filters.year_max)
  if filters.test_type is not None:
    stmt = stmt.where(Exam.test_type == filters.test_type)
  if filters.phase is not None:
    stmt = stmt.where(Exam.phase == filters.phase)
  if filters.discipline:
    stmt = stmt.where(Question.discipline.in_(filters.discipline))
  if filters.format_type is not None:
    stmt = stmt.where(Question.format_type == filters.format_type)
  if filters.has_figure is not None:
    stmt = stmt.where(Question.has_figure.is_(filters.has_figure))
  if filters.review_status:
    stmt = stmt.where(Question.review_status.in_(filters.review_status))
  if filters.has_answer is not None:
    exists = (
      select(Alternative.id)
      .where(Alternative.question_id == Question.id, Alternative.is_correct.is_(True))
      .exists()
    )
    stmt = stmt.where(exists if filters.has_answer else ~exists)
  return stmt


def _apply_order(stmt: Select, order: str) -> Select:
  """Ordenação feita no banco.

  `relevance` não aparece aqui: a ordem vem do bm25 do FTS5, e reproduzi-la em
  SQL exigiria um CASE com um ramo por id. A lista de ids cabe em memória, então
  list_questions ordena em Python nesse caso.
  """
  if order == "random":
    return stmt.order_by(func.random())
  if order == "year_asc":
    return stmt.order_by(Exam.year.asc(), Question.number.asc())
  return stmt.order_by(Exam.year.desc(), Question.number.asc())


async def list_questions(
  session: AsyncSession,
  filters: QuestionFilters,
  *,
  page: int,
  page_size: int,
) -> tuple[list[Question], int, dict[int, str]]:
  """Página de questões, o total e os trechos destacados da busca."""
  snippets: dict[int, str] = {}
  ranked_ids: list[int] | None = None

  if filters.q:
    hits = await search_question_ids(session, filters.q)
    if not hits:
      return [], 0, {}
    ranked_ids = [qid for qid, _ in hits]
    snippets = dict(hits)

  base = _apply_filters(select(Question), filters)
  if ranked_ids is not None:
    base = base.where(Question.id.in_(ranked_ids))

  total = await session.scalar(
    select(func.count()).select_from(base.with_only_columns(Question.id).subquery())
  )

  # `pages` é lido ao montar a URL da imagem no resumo. Sem carregar aqui, o
  # lazy load dispara durante a serialização, fora do contexto async, e o
  # SQLAlchemy levanta MissingGreenlet.
  stmt = base.options(selectinload(Question.exam), selectinload(Question.pages))
  if filters.order == "relevance" and ranked_ids:
    rows = (await session.scalars(stmt)).all()
    position = {qid: i for i, qid in enumerate(ranked_ids)}
    rows = sorted(rows, key=lambda r: position.get(r.id, len(position)))
    start = (page - 1) * page_size
    return list(rows[start : start + page_size]), total or 0, snippets

  stmt = _apply_order(stmt, filters.order)
  stmt = stmt.offset((page - 1) * page_size).limit(page_size)
  rows = (await session.scalars(stmt)).all()
  return list(rows), total or 0, snippets


async def get_question(session: AsyncSession, question_id: int) -> Question | None:
  stmt = (
    select(Question)
    .where(Question.id == question_id)
    .options(
      selectinload(Question.exam),
      selectinload(Question.alternatives),
      selectinload(Question.pages),
      selectinload(Question.assets),
      selectinload(Question.document).selectinload(Document.exam),
    )
  )
  return await session.scalar(stmt)


async def random_question(
  session: AsyncSession, filters: QuestionFilters
) -> Question | None:
  stmt = _apply_filters(select(Question), filters).order_by(func.random()).limit(1)
  question_id = await session.scalar(stmt.with_only_columns(Question.id))
  if question_id is None:
    return None
  return await get_question(session, question_id)
