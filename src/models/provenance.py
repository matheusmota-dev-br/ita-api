from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
  JSON,
  DateTime,
  ForeignKey,
  Integer,
  String,
  Text,
  UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base
from src.models.enums import NormalizationStatus, RunStatus, enum_col

if TYPE_CHECKING:
  from src.models.question import Question


class PipelineRun(Base):
  """Uma execução de um estágio do ETL, para rastrear o que produziu cada linha."""

  __tablename__ = "pipeline_runs"

  id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
  stage: Mapped[str] = mapped_column(String(32), nullable=False)
  pipeline_version: Mapped[str] = mapped_column(String(32), nullable=False)
  git_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
  started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
  finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
  status: Mapped[RunStatus] = mapped_column(
    enum_col(RunStatus), nullable=False, default=RunStatus.RUNNING
  )
  params: Mapped[Any | None] = mapped_column(JSON, nullable=True)
  stats: Mapped[Any | None] = mapped_column(JSON, nullable=True)


class LLMNormalization(Base):
  """Espelho consultável de uma resposta do LLM.

  Derivada, nunca autoritativa: a fonte de verdade é o cache em disco em
  data/cache/enrich/, para que `rm data/ita.db` não custe o gasto de API.
  """

  __tablename__ = "llm_normalizations"
  __table_args__ = (
    UniqueConstraint(
      "question_id", "cache_key", name="uq_llm_normalizations_question_id_cache_key"
    ),
  )

  id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
  question_id: Mapped[int] = mapped_column(
    ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
  )
  cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
  model: Mapped[str] = mapped_column(String(64), nullable=False)
  prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
  input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
  raw_response: Mapped[Any | None] = mapped_column(JSON, nullable=True)

  input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
  output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
  cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

  status: Mapped[NormalizationStatus] = mapped_column(
    enum_col(NormalizationStatus), nullable=False
  )
  error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
  run_id: Mapped[int | None] = mapped_column(
    ForeignKey("pipeline_runs.id", ondelete="SET NULL"), nullable=True
  )

  question: Mapped["Question"] = relationship()
