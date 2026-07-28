from pydantic import BaseModel, ConfigDict

from src.constants import Discipline, TestType
from src.models.enums import DocumentKind, Phase, ReviewStatus


class DocumentOut(BaseModel):
  """Um PDF, com o que a interface de auditoria precisa para listar e priorizar."""

  model_config = ConfigDict(from_attributes=True)

  id: int
  exam_id: int
  exam_slug: str
  test_type: TestType
  year: int
  phase: Phase
  discipline: Discipline | None = None
  kind: DocumentKind
  source_column: str
  local_path: str
  page_count: int | None = None

  question_count: int = 0
  reviewed_count: int = 0
  rejected_count: int = 0
  with_answer_count: int = 0
  answer_count: int = 0

  @property
  def pending_count(self) -> int:
    return self.question_count - self.reviewed_count - self.rejected_count


class BBoxIn(BaseModel):
  """Retângulo corrigido de uma parte da questão, em pontos do PDF."""

  page_no: int
  part_index: int = 0
  x0: float
  y0: float
  x1: float
  y1: float


class ReviewRequest(BaseModel):
  """Tudo é opcional: a interface envia só o que o revisor mudou.

  Um PATCH em vez de um PUT — marcar "não precisa de LLM" não pode apagar uma
  correção de texto feita antes.
  """

  status: ReviewStatus | None = None
  note: str | None = None
  needs_llm: bool | None = None
  statement_markdown: str | None = None
  alternatives: dict[str, str] | None = None
  bbox: BBoxIn | None = None


class ReviewResponse(BaseModel):
  question_id: int
  status: ReviewStatus
  note: str | None = None
  needs_llm: bool | None = None
  recropped: bool = False
  persisted_to: str


class ReviewProgress(BaseModel):
  total: int
  reviewed: int
  rejected: int
  pending: int
