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

from src.models.base import Base
from src.models.enums import ReviewStatus, enum_col

if TYPE_CHECKING:
  from src.models.question import Question

OPTION_LETTERS = ("A", "B", "C", "D", "E")


class Alternative(Base):
  """Uma alternativa A–E de uma questão objetiva.

  `is_correct` é NULL enquanto o gabarito não foi parseado — distinto de False,
  que significa "o gabarito diz que esta não é a correta".
  """

  __tablename__ = "alternatives"
  __table_args__ = (
    UniqueConstraint(
      "question_id", "letter", name="uq_alternatives_question_id_letter"
    ),
    CheckConstraint("letter IN ('A','B','C','D','E')", name="letter"),
    # Índice parcial: no máximo uma alternativa correta por questão.
    Index(
      "uq_alternatives_one_correct",
      "question_id",
      unique=True,
      sqlite_where=text("is_correct = 1"),
    ),
  )

  id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
  question_id: Mapped[int] = mapped_column(
    ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
  )
  letter: Mapped[str] = mapped_column(String(1), nullable=False)
  order_index: Mapped[int] = mapped_column(Integer, nullable=False)

  text_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
  text_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)

  # A alternativa é só uma figura (comum em geometria e circuitos).
  is_figure_only: Mapped[bool] = mapped_column(
    Boolean, nullable=False, default=False, server_default=text("0")
  )
  is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

  review_status: Mapped[ReviewStatus] = mapped_column(
    enum_col(ReviewStatus), nullable=False, default=ReviewStatus.RAW
  )

  question: Mapped["Question"] = relationship(back_populates="alternatives")
