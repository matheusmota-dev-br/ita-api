from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants import Discipline
from src.models.base import Base
from src.models.enums import DocumentKind, enum_col

if TYPE_CHECKING:
  from src.models.answer_key import AnswerKeyEntry
  from src.models.exam import Exam
  from src.models.question import Question
  from src.models.support_text import SupportText


class Document(Base):
  """Um PDF baixado.

  `discipline` é NULL para a prova combinada de 1ª fase, que cobre as cinco
  matérias num arquivo só. `source_column` guarda a coluna crua da tabela do
  site (`prova_1f`, `answer_key_2f`, `fisica`…), que é o que permite rastrear
  de qual das três eras do site o arquivo veio.
  """

  __tablename__ = "documents"
  __table_args__ = (
    UniqueConstraint(
      "exam_id",
      "kind",
      "discipline",
      "source_column",
      name="uq_documents_exam_id_kind_discipline_source_column",
    ),
    # Não é único: o mesmo PDF de gabarito é linkado por duas colunas em alguns anos.
    Index("ix_documents_sha256", "sha256"),
  )

  id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
  exam_id: Mapped[int] = mapped_column(
    ForeignKey("exams.id", ondelete="CASCADE"), nullable=False
  )
  kind: Mapped[DocumentKind] = mapped_column(enum_col(DocumentKind), nullable=False)
  discipline: Mapped[Discipline | None] = mapped_column(
    enum_col(Discipline), nullable=True
  )
  source_column: Mapped[str] = mapped_column(String(64), nullable=False)
  source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
  local_path: Mapped[str] = mapped_column(String(512), nullable=False)
  sha256: Mapped[str] = mapped_column(String(64), nullable=False)
  page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
  processed_dir: Mapped[str | None] = mapped_column(String(512), nullable=True)

  exam: Mapped["Exam"] = relationship(back_populates="documents")
  questions: Mapped[list["Question"]] = relationship(
    back_populates="document", cascade="all, delete-orphan"
  )
  support_texts: Mapped[list["SupportText"]] = relationship(
    back_populates="document", cascade="all, delete-orphan"
  )
  answer_key_entries: Mapped[list["AnswerKeyEntry"]] = relationship(
    back_populates="document", cascade="all, delete-orphan"
  )
