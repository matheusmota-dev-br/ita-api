from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import PROJECT_ROOT
from src.database import get_db
from src.models import Question
from src.models.question import QuestionPage
from src.repositories.documents import (
  document_answers,
  document_question_ids,
  get_document,
  list_documents,
  review_progress,
)
from src.schemas.document import DocumentOut, ReviewProgress

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

# Renderização de página: resolução suficiente para conferir texto na tela sem
# gerar PNGs de vários MB a cada scroll.
DEFAULT_PAGE_DPI = 110
MAX_PAGE_DPI = 200

BOX_COLOR = (0.85, 0.1, 0.1)
BOX_WIDTH = 1.2
HIGHLIGHT_COLOR = (0.1, 0.45, 0.95)
HIGHLIGHT_WIDTH = 2.0


@router.get("", response_model=list[DocumentOut])
async def list_endpoint(
  session: Annotated[AsyncSession, Depends(get_db)],
) -> list[DocumentOut]:
  return [DocumentOut(**row) for row in await list_documents(session)]


@router.get("/progress", response_model=ReviewProgress)
async def progress_endpoint(
  session: Annotated[AsyncSession, Depends(get_db)],
) -> ReviewProgress:
  return ReviewProgress(**await review_progress(session))


@router.get("/{document_id}/questions", response_model=list[int])
async def question_ids_endpoint(
  document_id: int,
  session: Annotated[AsyncSession, Depends(get_db)],
) -> list[int]:
  return await document_question_ids(session, document_id)


@router.get("/{document_id}/answers", response_model=list[dict])
async def answers_endpoint(
  document_id: int,
  session: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
  """Respostas parseadas de um gabarito, para conferir contra o PDF."""
  return await document_answers(session, document_id)


@router.get("/{document_id}/pages/{page_no}/image")
async def page_image_endpoint(
  document_id: int,
  page_no: int,
  session: Annotated[AsyncSession, Depends(get_db)],
  boxes: Annotated[
    bool, Query(description="desenha os retangulos das questoes")
  ] = True,
  highlight: Annotated[
    int | None, Query(description="numero da questao a destacar")
  ] = None,
  dpi: Annotated[int, Query(ge=40, le=MAX_PAGE_DPI)] = DEFAULT_PAGE_DPI,
) -> Response:
  """Renderiza uma página do PDF original com as regiões detectadas marcadas.

  É o que permite auditar a detecção: ver onde o pipeline achou que cada questão
  começa e termina, sobre a página real.
  """
  try:
    import pymupdf
  except ImportError as exc:  # pragma: no cover - depende do ambiente
    raise HTTPException(
      501, "pymupdf nao instalado; rode `pipenv install --dev`"
    ) from exc

  document = await get_document(session, document_id)
  if document is None:
    raise HTTPException(404, "documento nao encontrado")

  path = Path(document.local_path)
  if not path.is_absolute():
    path = PROJECT_ROOT / path
  if not path.is_file():
    raise HTTPException(404, f"PDF ausente em disco: {document.local_path}")

  rows = list(
    await session.execute(
      select(QuestionPage, Question.number)
      .join(Question, Question.id == QuestionPage.question_id)
      .where(Question.document_id == document_id, QuestionPage.page_no == page_no)
    )
  )

  doc = pymupdf.open(path)
  try:
    if not 0 <= page_no < len(doc):
      raise HTTPException(404, f"pagina {page_no} fora do documento")
    page = doc[page_no]
    if boxes:
      for question_page, number in rows:
        rect = pymupdf.Rect(
          question_page.x0, question_page.y0, question_page.x1, question_page.y1
        )
        is_highlight = highlight is not None and number == highlight
        page.draw_rect(
          rect,
          color=HIGHLIGHT_COLOR if is_highlight else BOX_COLOR,
          width=HIGHLIGHT_WIDTH if is_highlight else BOX_WIDTH,
          overlay=True,
        )
    png = page.get_pixmap(dpi=dpi, alpha=False).tobytes("png")
  finally:
    doc.close()

  return Response(
    content=png,
    media_type="image/png",
    headers={
      # O sha256 do PDF mais os parâmetros de render identificam o resultado.
      "ETag": f'"{document.sha256}-{page_no}-{int(boxes)}-{highlight}-{dpi}"',
      "Cache-Control": "private, max-age=3600",
    },
  )
