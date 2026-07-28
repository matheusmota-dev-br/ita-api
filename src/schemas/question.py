from pydantic import BaseModel, ConfigDict

from src.constants import Discipline, FormatType, TestType
from src.models.enums import DisciplineSource, Phase, ReviewStatus

PREVIEW_LENGTH = 240


class AlternativeOut(BaseModel):
  model_config = ConfigDict(from_attributes=True)

  letter: str
  text_markdown: str | None = None
  text_raw: str | None = None
  is_correct: bool | None = None
  is_figure_only: bool = False
  review_status: ReviewStatus


class QuestionPageOut(BaseModel):
  model_config = ConfigDict(from_attributes=True)

  part_index: int
  page_no: int
  bbox: list[float]
  image_url: str | None = None


class ExamOut(BaseModel):
  model_config = ConfigDict(from_attributes=True)

  id: int
  slug: str
  test_type: TestType
  year: int
  phase: Phase
  question_count: int | None = None


class SourceOut(BaseModel):
  document_id: int
  kind: str
  source_url: str | None = None
  local_path: str
  page_start: int
  page_end: int


class QuestionSummary(BaseModel):
  """Versão enxuta para listagem.

  Omite `statement_raw` e o corpo das alternativas de propósito: uma página de
  100 itens com o texto completo de cada questão passaria de vários megabytes.
  """

  model_config = ConfigDict(from_attributes=True)

  id: int
  year: int
  phase: Phase
  test_type: TestType
  discipline: Discipline | None = None
  number: int
  format_type: FormatType
  statement_preview: str
  has_figure: bool | None = None
  image_url: str | None = None
  review_status: ReviewStatus
  # Override manual da triagem: True força o enrich, False o dispensa, ausente
  # deixa a heurística decidir. Estava faltando no schema, então a interface
  # gravava o valor e nunca o lia de volta — as marcas "precisa/dispensa LLM"
  # não apareciam e os botões nunca ficavam destacados.
  needs_llm: bool | None = None
  # Preenchido só quando a busca full-text foi usada.
  excerpt: str | None = None


class QuestionDetail(QuestionSummary):
  statement_markdown: str | None = None
  statement_raw: str
  discipline_source: DisciplineSource | None = None
  discipline_confidence: float | None = None
  alternatives: list[AlternativeOut] = []
  pages: list[QuestionPageOut] = []
  source: SourceOut | None = None


class DisciplineCount(BaseModel):
  discipline: Discipline | None = None
  count: int


class YearCount(BaseModel):
  year: int
  count: int


class StatsOut(BaseModel):
  questions: int
  exams: int
  documents: int
  with_answer: int
  with_markdown: int
  by_discipline: list[DisciplineCount]
  by_year: list[YearCount]
  by_review_status: dict[str, int]
