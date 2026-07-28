from typing import TYPE_CHECKING

from sqlalchemy import (
  Boolean,
  CheckConstraint,
  ForeignKey,
  Index,
  Integer,
  String,
  Text,
  UniqueConstraint,
  text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants import Discipline
from src.models.base import Base
from src.models.enums import enum_col

if TYPE_CHECKING:
  from src.models.document import Document
  from src.models.exam import Exam


class AnswerKeyEntry(Base):
  """Uma linha crua do PDF de gabarito.

  Continua existindo depois de ser projetada em Alternative.is_correct: é a
  trilha de auditoria quando o parse sai errado, e a fonte de fallback para
  inferir a matéria das questões de 1ª fase (gabaritos costumam agrupar por
  matéria).
  """

  __tablename__ = "answer_key_entries"
  __table_args__ = (
    # A coluna entra na chave porque a matéria pode ser NULL: em um gabarito
    # cujos cabeçalhos não puderam ser casados, duas respostas de número 1 em
    # colunas diferentes teriam a mesma chave e uma sobrescreveria a outra.
    UniqueConstraint(
      "document_id",
      "question_number",
      "discipline",
      "column_index",
      name="uq_answer_key_entries_document_id_question_number_discipline_column_index",
    ),
    CheckConstraint(
      "correct_letter IS NULL OR correct_letter IN ('A','B','C','D','E')",
      name="correct_letter",
    ),
    Index("ix_answer_key_entries_exam_id", "exam_id"),
  )

  id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
  document_id: Mapped[int] = mapped_column(
    ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
  )
  exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id"), nullable=False)

  question_number: Mapped[int] = mapped_column(Integer, nullable=False)
  discipline: Mapped[Discipline | None] = mapped_column(
    enum_col(Discipline), nullable=True
  )
  # Índice da coluna da tabela do gabarito, da esquerda para a direita.
  # Preserva a identidade da matéria mesmo quando o cabeçalho não foi casado.
  column_index: Mapped[int] = mapped_column(
    Integer, nullable=False, default=0, server_default=text("0")
  )
  correct_letter: Mapped[str | None] = mapped_column(String(1), nullable=True)
  annulled: Mapped[bool] = mapped_column(
    Boolean, nullable=False, default=False, server_default=text("0")
  )
  raw_line: Mapped[str | None] = mapped_column(Text, nullable=True)

  document: Mapped["Document"] = relationship(back_populates="answer_key_entries")
  exam: Mapped["Exam"] = relationship()
