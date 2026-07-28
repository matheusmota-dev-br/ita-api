from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.models import Alternative, Document, Exam, Question
from src.schemas.question import DisciplineCount, StatsOut, YearCount

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])


@router.get("", response_model=StatsOut)
async def get_stats(session: Annotated[AsyncSession, Depends(get_db)]) -> StatsOut:
  questions = await session.scalar(select(func.count()).select_from(Question))
  exams = await session.scalar(select(func.count()).select_from(Exam))
  documents = await session.scalar(select(func.count()).select_from(Document))

  with_answer = await session.scalar(
    select(func.count(func.distinct(Alternative.question_id))).where(
      Alternative.is_correct.is_(True)
    )
  )
  with_markdown = await session.scalar(
    select(func.count())
    .select_from(Question)
    .where(Question.statement_markdown.is_not(None))
  )

  by_discipline = [
    DisciplineCount(discipline=row[0], count=row[1])
    for row in await session.execute(
      select(Question.discipline, func.count())
      .group_by(Question.discipline)
      .order_by(func.count().desc())
    )
  ]
  by_year = [
    YearCount(year=row[0], count=row[1])
    for row in await session.execute(
      select(Exam.year, func.count())
      .join(Question, Question.exam_id == Exam.id)
      .group_by(Exam.year)
      .order_by(Exam.year.desc())
    )
  ]
  by_review = {
    row[0].value: row[1]
    for row in await session.execute(
      select(Question.review_status, func.count()).group_by(Question.review_status)
    )
  }

  return StatsOut(
    questions=questions or 0,
    exams=exams or 0,
    documents=documents or 0,
    with_answer=with_answer or 0,
    with_markdown=with_markdown or 0,
    by_discipline=by_discipline,
    by_year=by_year,
    by_review_status=by_review,
  )
