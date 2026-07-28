from typing import TYPE_CHECKING

from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants import TestType
from src.models.base import Base
from src.models.enums import Phase, enum_col

if TYPE_CHECKING:
  from src.models.document import Document
  from src.models.question import Question


class Exam(Base):
  """Uma prova lógica: (tipo, ano, fase).

  Não é um PDF — a 1ª fase é um PDF cobrindo cinco matérias, a 2ª fase são
  vários PDFs por matéria, e os gabaritos são PDFs à parte. Todos apontam para
  o mesmo Exam. Ver Document.
  """

  __tablename__ = "exams"
  __table_args__ = (
    UniqueConstraint(
      "test_type", "year", "phase", name="uq_exams_test_type_year_phase"
    ),
    Index("ix_exams_year", "year"),
  )

  id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
  test_type: Mapped[TestType] = mapped_column(enum_col(TestType), nullable=False)
  year: Mapped[int] = mapped_column(Integer, nullable=False)
  phase: Mapped[Phase] = mapped_column(enum_col(Phase), nullable=False)
  source_page_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

  documents: Mapped[list["Document"]] = relationship(
    back_populates="exam", cascade="all, delete-orphan"
  )
  questions: Mapped[list["Question"]] = relationship(back_populates="exam")

  @property
  def slug(self) -> str:
    return f"{self.test_type.value}-{self.year}-{self.phase.value}"
