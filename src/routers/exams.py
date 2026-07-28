from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Discipline, TestType
from src.database import get_db
from src.models import Exam, Question
from src.models.enums import Phase
from src.schemas.question import ExamOut

router = APIRouter(prefix="/api/v1/exams", tags=["exams"])


@router.get("", response_model=list[ExamOut])
async def list_exams(
  session: Annotated[AsyncSession, Depends(get_db)],
  year: int | None = None,
  year_min: int | None = None,
  year_max: int | None = None,
  test_type: TestType | None = None,
  phase: Phase | None = None,
  discipline: Annotated[Discipline | None, Query()] = None,
) -> list[ExamOut]:
  counts = (
    select(Question.exam_id, func.count().label("total"))
    .group_by(Question.exam_id)
    .subquery()
  )
  stmt = select(Exam, counts.c.total).outerjoin(counts, counts.c.exam_id == Exam.id)

  if year is not None:
    stmt = stmt.where(Exam.year == year)
  if year_min is not None:
    stmt = stmt.where(Exam.year >= year_min)
  if year_max is not None:
    stmt = stmt.where(Exam.year <= year_max)
  if test_type is not None:
    stmt = stmt.where(Exam.test_type == test_type)
  if phase is not None:
    stmt = stmt.where(Exam.phase == phase)
  if discipline is not None:
    stmt = stmt.where(
      select(Question.id)
      .where(Question.exam_id == Exam.id, Question.discipline == discipline)
      .exists()
    )

  stmt = stmt.order_by(Exam.year.desc(), Exam.phase)
  rows = (await session.execute(stmt)).all()
  return [
    ExamOut(
      id=exam.id,
      slug=exam.slug,
      test_type=exam.test_type,
      year=exam.year,
      phase=exam.phase,
      question_count=total or 0,
    )
    for exam, total in rows
  ]


@router.get("/{slug}", response_model=ExamOut)
async def get_exam(
  slug: str,
  session: Annotated[AsyncSession, Depends(get_db)],
) -> ExamOut:
  exams = (await session.scalars(select(Exam))).all()
  exam = next((e for e in exams if e.slug == slug), None)
  if exam is None:
    raise HTTPException(404, "prova nao encontrada")

  total = await session.scalar(
    select(func.count()).select_from(Question).where(Question.exam_id == exam.id)
  )
  return ExamOut(
    id=exam.id,
    slug=exam.slug,
    test_type=exam.test_type,
    year=exam.year,
    phase=exam.phase,
    question_count=total or 0,
  )
