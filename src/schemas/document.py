from pydantic import BaseModel, ConfigDict, Field

from src.constants import Discipline, TestType
from src.etl.transform.batch import MAX_QUESTIONS_CEILING
from src.models.enums import DocumentKind, Phase, ReviewStatus

# O mesmo teto que o detector usa em lote. Sanidade contra número digitado
# errado, não uma afirmação sobre quantas questões uma prova tem — a maior do
# corpus tem 70, e `MAX_QUESTIONS = 60` é só o default da função de detecção.
MAX_QUESTION_NUMBER = MAX_QUESTIONS_CEILING


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


class CreateQuestionRequest(BaseModel):
  """Questão criada à mão a partir de uma região da página.

  O recorte é a entrada: o número e o retângulo bastam, porque texto e imagem
  saem do próprio retângulo — a mesma rasterização que o transform usa.
  """

  number: int = Field(ge=1, le=MAX_QUESTION_NUMBER)
  bbox: BBoxIn


class RenumberRequest(BaseModel):
  """Novo número de uma questão.

  Se o destino já existir, as duas trocam de número em vez de a operação
  falhar: "trocar de ordem" é o caso comum, e obrigar a passar por um número
  livre intermediário só transformaria uma ação em três.
  """

  number: int = Field(ge=1, le=MAX_QUESTION_NUMBER)


class AutofillOut(BaseModel):
  """Sugestão de transcrição derivada do texto bruto, sem LLM.

  É uma *sugestão*: separar enunciado de alternativas é ambíguo só pelo texto —
  uma questão discursiva com itens a)–e) é indistinguível de uma objetiva — e
  por isso quem decide é quem está auditando, vendo o recorte ao lado.
  """

  statement: str
  alternatives: dict[str, str]
  style: str | None = None
  complete: bool


class MutationResponse(BaseModel):
  """Resultado de uma operação que muda quais questões existem."""

  document_id: int
  question_id: int | None = None
  number: int
  operation: str
  swapped_with: int | None = None
  persisted_to: str
