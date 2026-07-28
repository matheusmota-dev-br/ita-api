from typing import TYPE_CHECKING

from sqlalchemy import Column, Float, ForeignKey, String, Table, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants import Discipline
from src.models.base import Base
from src.models.enums import DisciplineSource, enum_col

if TYPE_CHECKING:
  from src.models.question import Question


question_topics = Table(
  "question_topics",
  Base.metadata,
  Column(
    "question_id", ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True
  ),
  Column("topic_id", ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True),
  Column("source", enum_col(DisciplineSource), nullable=True),
  Column("confidence", Float, nullable=True),
)


class Topic(Base):
  """Assunto de uma questão.

  O slug é a chave natural: o loader faz upsert por slug para que "Geometria
  Analítica" e "geometria-analitica" emitidos pelo LLM em execuções diferentes
  convirjam para a mesma linha.
  """

  __tablename__ = "topics"
  __table_args__ = (UniqueConstraint("slug", name="uq_topics_slug"),)

  id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
  slug: Mapped[str] = mapped_column(String(128), nullable=False)
  label: Mapped[str] = mapped_column(String(128), nullable=False)
  discipline: Mapped[Discipline | None] = mapped_column(
    enum_col(Discipline), nullable=True
  )
  parent_id: Mapped[int | None] = mapped_column(
    ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
  )

  parent: Mapped["Topic | None"] = relationship(remote_side="Topic.id")
  questions: Mapped[list["Question"]] = relationship(
    secondary=question_topics, back_populates="topics"
  )
