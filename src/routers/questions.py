from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.constants import Discipline, FormatType, TestType
from src.database import get_db
from src.models import Question
from src.models.enums import Phase, ReviewStatus
from src.repositories.questions import (
  QuestionFilters,
  get_question,
  list_questions,
  random_question,
)
from src.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from src.schemas.question import (
  PREVIEW_LENGTH,
  AlternativeOut,
  QuestionDetail,
  QuestionPageOut,
  QuestionSummary,
  SourceOut,
)

router = APIRouter(prefix="/api/v1/questions", tags=["questions"])

Order = Literal["year_desc", "year_asc", "relevance", "random"]


def question_filters(
  q: Annotated[str | None, Query(description="busca full-text no enunciado")] = None,
  year: int | None = None,
  year_min: int | None = None,
  year_max: int | None = None,
  test_type: TestType | None = None,
  phase: Phase | None = None,
  discipline: Annotated[list[Discipline] | None, Query()] = None,
  format_type: FormatType | None = None,
  has_figure: bool | None = None,
  has_answer: Annotated[
    bool | None, Query(description="apenas questões com gabarito conhecido")
  ] = None,
  topic: Annotated[str | None, Query(description="slug do assunto")] = None,
  review_status: Annotated[list[ReviewStatus] | None, Query()] = None,
  order: Order = "year_desc",
) -> QuestionFilters:
  if order == "relevance" and not q:
    raise HTTPException(422, "order=relevance exige o parametro q")
  return QuestionFilters(
    q=q,
    year=year,
    year_min=year_min,
    year_max=year_max,
    test_type=test_type,
    phase=phase,
    discipline=discipline or [],
    format_type=format_type,
    has_figure=has_figure,
    topic=topic,
    has_answer=has_answer,
    review_status=review_status or [],
    order=order,
  )


def _image_url(question: Question, part: int = 0) -> str | None:
  if not question.pages:
    return None
  return f"/api/v1/questions/{question.id}/image?part={part}"


def _to_summary(question: Question, excerpt: str | None = None) -> QuestionSummary:
  statement = question.statement_markdown or question.statement_raw or ""
  return QuestionSummary(
    id=question.id,
    year=question.exam.year,
    phase=question.exam.phase,
    test_type=question.exam.test_type,
    discipline=question.discipline,
    number=question.number,
    format_type=question.format_type,
    statement_preview=statement[:PREVIEW_LENGTH],
    has_figure=question.has_figure,
    image_url=_image_url(question),
    review_status=question.review_status,
    needs_llm=question.needs_llm,
    excerpt=excerpt,
  )


@router.get("", response_model=Page[QuestionSummary])
async def list_endpoint(
  filters: Annotated[QuestionFilters, Depends(question_filters)],
  session: Annotated[AsyncSession, Depends(get_db)],
  page: Annotated[int, Query(ge=1)] = 1,
  page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> Page[QuestionSummary]:
  rows, total, snippets = await list_questions(
    session, filters, page=page, page_size=page_size
  )
  items = [_to_summary(row, snippets.get(row.id)) for row in rows]
  return Page.build(items, total, page, page_size)


# Precisa vir antes de /{question_id}: caso contrário o FastAPI tenta converter
# "random" para int e devolve 422.
@router.get("/random", response_model=QuestionDetail)
async def random_endpoint(
  filters: Annotated[QuestionFilters, Depends(question_filters)],
  session: Annotated[AsyncSession, Depends(get_db)],
) -> QuestionDetail:
  question = await random_question(session, filters)
  if question is None:
    raise HTTPException(404, "nenhuma questao corresponde aos filtros")
  return _to_detail(question)


def _to_detail(question: Question) -> QuestionDetail:
  summary = _to_summary(question)
  return QuestionDetail(
    **summary.model_dump(),
    statement_markdown=question.statement_markdown,
    statement_raw=question.statement_raw,
    discipline_source=question.discipline_source,
    discipline_confidence=question.discipline_confidence,
    alternatives=[AlternativeOut.model_validate(a) for a in question.alternatives],
    pages=[
      QuestionPageOut(
        part_index=p.part_index,
        page_no=p.page_no,
        bbox=[p.x0, p.y0, p.x1, p.y1],
        image_url=_image_url(question, p.part_index),
      )
      for p in question.pages
    ],
    source=SourceOut(
      document_id=question.document.id,
      kind=question.document.kind.value,
      source_url=question.document.source_url,
      local_path=question.document.local_path,
      page_start=question.page_start,
      page_end=question.page_end,
    ),
  )


@router.get("/{question_id}", response_model=QuestionDetail)
async def detail_endpoint(
  question_id: int,
  session: Annotated[AsyncSession, Depends(get_db)],
) -> QuestionDetail:
  question = await get_question(session, question_id)
  if question is None:
    raise HTTPException(404, "questao nao encontrada")
  return _to_detail(question)


@router.get("/{question_id}/image", response_class=FileResponse)
async def image_endpoint(
  question_id: int,
  session: Annotated[AsyncSession, Depends(get_db)],
  part: Annotated[int, Query(ge=0)] = 0,
) -> Response:
  question = await get_question(session, question_id)
  if question is None:
    raise HTTPException(404, "questao nao encontrada")

  # Uma questão que atravessa páginas tem um recorte por parte; a ordem por
  # caminho reproduz a ordem das partes (questao_07_p1.png, _p2.png, ...).
  assets = sorted(question.assets, key=lambda a: a.relative_path)
  if not assets:
    raise HTTPException(404, "questao sem imagem")
  if part >= len(assets):
    raise HTTPException(
      404, f"questao tem {len(assets)} parte(s); part={part} nao existe"
    )
  asset = assets[part]

  # `relative_path` é escrito pelo ETL; sem a checagem de containment um
  # manifest defeituoso viraria path traversal.
  media_root = settings.media_root.resolve()
  path = (media_root / asset.relative_path).resolve()
  if not path.is_relative_to(media_root) or not path.is_file():
    raise HTTPException(404, "imagem indisponivel")

  return FileResponse(
    path,
    media_type="image/png",
    headers={
      "Cache-Control": "public, max-age=31536000, immutable",
      "ETag": f'"{asset.sha256}"',
    },
  )
