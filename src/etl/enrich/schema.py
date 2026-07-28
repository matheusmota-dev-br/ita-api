"""Formato estruturado que o LLM devolve para cada questão.

Restrições do structured outputs que moldam este schema: todo objeto precisa de
`additionalProperties: false`, toda propriedade precisa estar em `required`, não
há recursão, e restrições numéricas ou de tamanho (`minimum`, `maxLength`) são
ignoradas silenciosamente. Por isso `confidence` é um enum e não um float 0–1.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

Confidence = Literal["high", "medium", "low"]
DetectedDiscipline = Literal[
  "matematica", "fisica", "quimica", "portugues", "ingles", "indeterminado"
]


class NormalizedAlternative(BaseModel):
  model_config = ConfigDict(extra="forbid")

  letter: Literal["A", "B", "C", "D", "E"]
  text_markdown: str
  is_figure_only: bool


class NormalizedQuestion(BaseModel):
  model_config = ConfigDict(extra="forbid")

  question_number: int
  question_type: Literal["objective", "dissertative"]
  statement_markdown: str
  alternatives: list[NormalizedAlternative]
  detected_discipline: DetectedDiscipline
  detected_topics: list[str]
  has_figure: bool
  figure_descriptions: list[str]
  transcription_confidence: Confidence
  notes: str
