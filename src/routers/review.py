import hashlib
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import PROJECT_ROOT, settings
from src.database import get_db
from src.etl.load.decisions import record_decision
from src.models import Question
from src.models.enums import ReviewStatus
from src.repositories.questions import get_question
from src.schemas.document import BBoxIn, ReviewRequest, ReviewResponse

router = APIRouter(tags=["review"])

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Mesma resolução que o transform usa, para que um recorte corrigido à mão
# fique indistinguível de um gerado pelo pipeline.
CROP_DPI = 150
MIN_SIDE = 2.0


@router.get("/review", include_in_schema=False)
async def review_ui() -> FileResponse:
  """Interface de auditoria, servida pela própria API."""
  return FileResponse(STATIC_DIR / "review.html", media_type="text/html")


def _recrop(question: Question, bbox: BBoxIn) -> bool:
  """Regera o PNG do recorte a partir do retângulo corrigido.

  Sobrescreve o arquivo no lugar, para que a URL da imagem e o caminho em
  `assets` continuem válidos. Devolve False quando não há o que regerar.
  """
  import pymupdf

  if bbox.x1 - bbox.x0 < MIN_SIDE or bbox.y1 - bbox.y0 < MIN_SIDE:
    raise HTTPException(422, "retangulo sem area")

  page_row = next((p for p in question.pages if p.part_index == bbox.part_index), None)
  asset = next(
    (
      a
      for a in sorted(question.assets, key=lambda a: a.relative_path)
      if a.relative_path
    ),
    None,
  )
  if page_row is None or asset is None:
    return False

  pdf_path = Path(question.document.local_path)
  if not pdf_path.is_absolute():
    pdf_path = PROJECT_ROOT / pdf_path
  if not pdf_path.is_file():
    raise HTTPException(404, f"PDF ausente: {question.document.local_path}")

  target = (settings.media_root.resolve() / asset.relative_path).resolve()
  if not target.is_relative_to(settings.media_root.resolve()):
    raise HTTPException(400, "caminho de imagem invalido")

  doc = pymupdf.open(pdf_path)
  try:
    if not 0 <= bbox.page_no < len(doc):
      raise HTTPException(422, f"pagina {bbox.page_no} fora do documento")
    page = doc[bbox.page_no]
    rect = pymupdf.Rect(bbox.x0, bbox.y0, bbox.x1, bbox.y1)
    pixmap = page.get_pixmap(clip=rect, dpi=CROP_DPI, alpha=False)
    png = pixmap.tobytes("png")
    # O texto também é reextraído: o recorte define o que é a questão.
    text = page.get_text(clip=rect, sort=True).strip()
  finally:
    doc.close()

  target.parent.mkdir(parents=True, exist_ok=True)
  target.write_bytes(png)

  page_row.page_no = bbox.page_no
  page_row.x0, page_row.y0 = bbox.x0, bbox.y0
  page_row.x1, page_row.y1 = bbox.x1, bbox.y1
  page_row.text_raw = text
  asset.sha256 = hashlib.sha256(png).hexdigest()
  asset.bbox = [bbox.x0, bbox.y0, bbox.x1, bbox.y1]
  asset.page_no = bbox.page_no
  asset.width_px, asset.height_px = pixmap.width, pixmap.height
  asset.byte_size = len(png)
  if text:
    question.statement_raw = text
  question.page_start = min(p.page_no for p in question.pages)
  question.page_end = max(p.page_no for p in question.pages)
  return True


def _apply_manual_text(question: Question, body: ReviewRequest) -> None:
  """Correção feita à mão entra como human_verified.

  O load protege linhas human_verified do upsert e do enrich, então uma
  questão corrigida aqui não é sobrescrita nem pelo LLM nem por um reload.
  """
  if body.statement_markdown is not None:
    question.statement_markdown = body.statement_markdown or None
    question.review_status = ReviewStatus.HUMAN_VERIFIED

  if body.alternatives:
    by_letter = {a.letter: a for a in question.alternatives}
    for letter, text in body.alternatives.items():
      alternative = by_letter.get(letter.upper())
      if alternative is not None:
        alternative.text_markdown = text or None
        alternative.review_status = ReviewStatus.HUMAN_VERIFIED


@router.post("/api/v1/questions/{question_id}/review", response_model=ReviewResponse)
async def submit_review(
  question_id: int,
  body: ReviewRequest,
  session: Annotated[AsyncSession, Depends(get_db)],
) -> ReviewResponse:
  """Registra a conferência de uma questão: veredito, triagem, texto e recorte.

  Grava nos dois lugares — no banco, para a API refletir na hora, e em
  data/review/decisions.json, que o load reaplica depois de um --rebuild.
  """
  question = await get_question(session, question_id)
  if question is None:
    raise HTTPException(404, "questao nao encontrada")

  recropped = False
  if body.bbox is not None:
    recropped = _recrop(question, body.bbox)

  _apply_manual_text(question, body)

  if body.needs_llm is not None:
    question.needs_llm = body.needs_llm
  if body.status is not None:
    question.review_status = body.status

  path = record_decision(
    local_path=question.document.local_path,
    number=question.number,
    status=question.review_status.value
    if body.status or body.statement_markdown
    else None,
    note=body.note,
    needs_llm=body.needs_llm,
    statement_markdown=body.statement_markdown,
    alternatives=body.alternatives,
    bbox=body.bbox.model_dump() if body.bbox else None,
  )
  await session.flush()

  return ReviewResponse(
    question_id=question_id,
    status=question.review_status,
    note=body.note,
    needs_llm=question.needs_llm,
    recropped=recropped,
    persisted_to=str(path),
  )
