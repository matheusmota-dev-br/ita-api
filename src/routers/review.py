from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import PROJECT_ROOT, settings
from src.database import get_db
from src.etl.load.decisions import (
  OP_CREATE,
  OP_DELETE,
  OP_RENUMBER,
  record_decision,
  record_operation,
)
from src.etl.load.manual import (
  build_manual_question,
  manual_relative_path,
  render_manual_crop,
)
from src.etl.transform.crop import Crop, CropError, render_crop, write_crop
from src.etl.transform.split import split_statement
from src.models import Document, Question
from src.models.enums import ReviewStatus
from src.repositories.documents import get_document
from src.repositories.questions import get_question
from src.schemas.document import (
  AutofillOut,
  BBoxIn,
  CreateQuestionRequest,
  MutationResponse,
  RenumberRequest,
  ReviewRequest,
  ReviewResponse,
)

router = APIRouter(tags=["review"])

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/review", include_in_schema=False)
async def review_ui() -> FileResponse:
  """Interface de auditoria, servida pela própria API."""
  return FileResponse(STATIC_DIR / "review.html", media_type="text/html")


@router.get("/review/progresso", include_in_schema=False)
async def progress_ui() -> FileResponse:
  """Painel de progresso detalhado da auditoria."""
  return FileResponse(STATIC_DIR / "progress.html", media_type="text/html")


def _pdf_path(document: Document) -> Path:
  path = Path(document.local_path)
  if not path.is_absolute():
    path = PROJECT_ROOT / path
  if not path.is_file():
    raise HTTPException(404, f"PDF ausente: {document.local_path}")
  return path


def _crop_or_422(pdf_path: Path, bbox: BBoxIn) -> Crop:
  try:
    return render_crop(pdf_path, bbox.page_no, (bbox.x0, bbox.y0, bbox.x1, bbox.y1))
  except CropError as exc:
    raise HTTPException(422, str(exc)) from exc


def _safe_target(relative_path: str) -> Path:
  root = settings.media_root.resolve()
  target = (root / relative_path).resolve()
  if not target.is_relative_to(root):
    raise HTTPException(400, "caminho de imagem invalido")
  return target


def _recrop(question: Question, bbox: BBoxIn) -> bool:
  """Regera o PNG do recorte a partir do retângulo corrigido.

  Sobrescreve o arquivo no lugar, para que a URL da imagem e o caminho em
  `assets` continuem válidos. Devolve False quando não há o que regerar.
  """
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

  crop = _crop_or_422(_pdf_path(question.document), bbox)
  write_crop(crop, _safe_target(asset.relative_path))

  page_row.page_no = bbox.page_no
  page_row.x0, page_row.y0 = bbox.x0, bbox.y0
  page_row.x1, page_row.y1 = bbox.x1, bbox.y1
  page_row.text_raw = crop.text
  asset.sha256 = crop.sha256
  asset.bbox = [bbox.x0, bbox.y0, bbox.x1, bbox.y1]
  asset.page_no = bbox.page_no
  asset.width_px, asset.height_px = crop.width_px, crop.height_px
  asset.byte_size = crop.byte_size
  if crop.text:
    question.statement_raw = crop.text
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


@router.get("/api/v1/questions/{question_id}/autofill", response_model=AutofillOut)
async def autofill_endpoint(
  question_id: int,
  session: Annotated[AsyncSession, Depends(get_db)],
) -> AutofillOut:
  """Sugere enunciado e alternativas a partir do texto bruto, sem chamar LLM.

  Nada é gravado: a interface mostra o resultado e quem audita decide. Rodar o
  parser no servidor, e não no navegador, é o que permite testá-lo contra o
  corpus real e reaproveitá-lo depois no ETL.
  """
  question = await get_question(session, question_id)
  if question is None:
    raise HTTPException(404, "questao nao encontrada")

  split = split_statement(question.statement_raw or "")
  return AutofillOut(
    statement=split.statement,
    alternatives=split.alternatives,
    style=split.style,
    complete=split.complete,
  )


@router.post(
  "/api/v1/documents/{document_id}/questions",
  response_model=MutationResponse,
  status_code=201,
)
async def create_question_endpoint(
  document_id: int,
  body: CreateQuestionRequest,
  session: Annotated[AsyncSession, Depends(get_db)],
) -> MutationResponse:
  """Cria uma questão a partir de uma região da página.

  Para os documentos em que a detecção falhou — capa digitalizada, layout fora
  do padrão — marcar a região à mão é mais rápido e mais barato que ajustar a
  heurística ou chamar o LLM. A questão nasce `human_verified`: veio de um
  humano apontando o retângulo.
  """
  document = await get_document(session, document_id)
  if document is None:
    raise HTTPException(404, "documento nao encontrado")

  taken = await session.scalar(
    select(func.count())
    .select_from(Question)
    .where(Question.document_id == document_id, Question.number == body.number)
  )
  if taken:
    raise HTTPException(409, f"ja existe a questao {body.number} neste documento")

  bbox = body.bbox.model_dump()
  # Valida o retângulo antes de escrever qualquer coisa em disco.
  _safe_target(manual_relative_path(document, body.number))
  try:
    crop, relative = render_manual_crop(
      _pdf_path(document), document, body.number, bbox
    )
  except CropError as exc:
    raise HTTPException(422, str(exc)) from exc

  question = build_manual_question(document, body.number, bbox, crop, relative)
  session.add(question)
  await session.flush()

  path = record_operation(
    op=OP_CREATE,
    local_path=document.local_path,
    number=body.number,
    bbox=body.bbox.model_dump(),
  )
  return MutationResponse(
    document_id=document.id,
    question_id=question.id,
    number=body.number,
    operation=OP_CREATE,
    persisted_to=str(path),
  )


@router.delete("/api/v1/questions/{question_id}", response_model=MutationResponse)
async def delete_question_endpoint(
  question_id: int,
  session: Annotated[AsyncSession, Depends(get_db)],
) -> MutationResponse:
  """Remove uma questão que a detecção inventou.

  O PNG em disco é deixado como está: apagar arquivo é irreversível, e um
  recorte órfão não custa nada. A exclusão fica registrada e é reaplicada no
  rebuild, então a questão não volta.
  """
  question = await get_question(session, question_id)
  if question is None:
    raise HTTPException(404, "questao nao encontrada")

  document_id, number = question.document_id, question.number
  local_path = question.document.local_path

  await session.delete(question)
  await session.flush()

  path = record_operation(op=OP_DELETE, local_path=local_path, number=number)
  return MutationResponse(
    document_id=document_id,
    number=number,
    operation=OP_DELETE,
    persisted_to=str(path),
  )


@router.post("/api/v1/questions/{question_id}/number", response_model=MutationResponse)
async def renumber_question_endpoint(
  question_id: int,
  body: RenumberRequest,
  session: Annotated[AsyncSession, Depends(get_db)],
) -> MutationResponse:
  """Muda o número de uma questão, trocando com a ocupante se houver.

  A troca é feita em duas etapas passando por um número negativo temporário:
  `UNIQUE(document_id, number)` recusaria o estado intermediário em que as duas
  questões têm o mesmo número.
  """
  question = await get_question(session, question_id)
  if question is None:
    raise HTTPException(404, "questao nao encontrada")
  if body.number == question.number:
    raise HTTPException(422, "o numero de destino e o atual")

  other = await session.scalar(
    select(Question).where(
      Question.document_id == question.document_id,
      Question.number == body.number,
    )
  )

  origin = question.number
  if other is not None:
    # Um flush por etapa. Atribuir os dois números e liberar um flush só
    # falharia: o SQLAlchemy emite os UPDATE em ordem de identidade, e o
    # primeiro deles já colidiria com o número que o outro ainda ocupa.
    other.number = -abs(origin)
    await session.flush()
    question.number = body.number
    await session.flush()
    other.number = origin
  else:
    question.number = body.number
  await session.flush()

  path = record_operation(
    op=OP_RENUMBER,
    local_path=question.document.local_path,
    number=origin,
    to=body.number,
    swap=other is not None,
  )
  return MutationResponse(
    document_id=question.document_id,
    question_id=question.id,
    number=body.number,
    operation=OP_RENUMBER,
    swapped_with=origin if other is not None else None,
    persisted_to=str(path),
  )
