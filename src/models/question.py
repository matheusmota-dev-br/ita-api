from typing import TYPE_CHECKING

from sqlalchemy import (
  Boolean,
  Float,
  ForeignKey,
  Index,
  Integer,
  String,
  Text,
  UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants import Discipline, FormatType
from src.models.base import Base
from src.models.enums import DisciplineSource, ReviewStatus, enum_col

if TYPE_CHECKING:
  from src.models.alternative import Alternative
  from src.models.asset import Asset
  from src.models.document import Document
  from src.models.exam import Exam
  from src.models.support_text import SupportText
  from src.models.topic import Topic


class Question(Base):
  """Uma questão.

  A chave natural é (document_id, number), não (exam_id, number): nas provas de
  2008–2018 há um PDF por matéria e a numeração reinicia em 1 em cada um.
  """

  __tablename__ = "questions"
  __table_args__ = (
    UniqueConstraint("document_id", "number", name="uq_questions_document_id_number"),
    Index("ix_questions_exam_id", "exam_id"),
    Index("ix_questions_discipline", "discipline"),
    Index("ix_questions_review_status", "review_status"),
    Index("ix_questions_exam_id_discipline", "exam_id", "discipline"),
  )

  id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
  document_id: Mapped[int] = mapped_column(
    ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
  )
  # Desnormalizado a partir de document.exam_id para filtrar sem JOIN.
  exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id"), nullable=False)

  number: Mapped[int] = mapped_column(Integer, nullable=False)
  format_type: Mapped[FormatType] = mapped_column(enum_col(FormatType), nullable=False)

  discipline: Mapped[Discipline | None] = mapped_column(
    enum_col(Discipline), nullable=True
  )
  discipline_source: Mapped[DisciplineSource | None] = mapped_column(
    enum_col(DisciplineSource), nullable=True
  )
  discipline_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

  # statement_raw é a extração verbatim do PyMuPDF e nunca é sobrescrita pelo LLM.
  statement_raw: Mapped[str] = mapped_column(Text, nullable=False)
  statement_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)

  page_start: Mapped[int] = mapped_column(Integer, nullable=False)
  page_end: Mapped[int] = mapped_column(Integer, nullable=False)

  has_figure: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

  # sha256(statement_raw + bytes de todos os recortes). É a chave de cache do LLM
  # e o que permite ao load pular escritas que não mudariam nada.
  source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

  review_status: Mapped[ReviewStatus] = mapped_column(
    enum_col(ReviewStatus), nullable=False, default=ReviewStatus.RAW
  )

  # Override manual da triagem do enrich. NULL deixa a heurística decidir;
  # True força o LLM mesmo em texto limpo; False dispensa — tipicamente porque
  # o revisor já corrigiu o enunciado à mão.
  needs_llm: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

  # Texto concatenado que alimenta o índice FTS5, montado pelo estágio de load.
  search_blob: Mapped[str | None] = mapped_column(Text, nullable=True)

  document: Mapped["Document"] = relationship(back_populates="questions")
  exam: Mapped["Exam"] = relationship(back_populates="questions")
  pages: Mapped[list["QuestionPage"]] = relationship(
    back_populates="question",
    cascade="all, delete-orphan",
    order_by="QuestionPage.part_index",
  )
  alternatives: Mapped[list["Alternative"]] = relationship(
    back_populates="question",
    cascade="all, delete-orphan",
    order_by="Alternative.letter",
  )
  assets: Mapped[list["Asset"]] = relationship(
    back_populates="question", cascade="all, delete-orphan"
  )
  support_texts: Mapped[list["SupportText"]] = relationship(
    secondary="question_support_texts", back_populates="questions"
  )
  topics: Mapped[list["Topic"]] = relationship(
    secondary="question_topics", back_populates="questions"
  )


class QuestionPage(Base):
  """Uma parte de uma questão numa página, com o bbox do recorte.

  Uma linha por (page_no, Rect) devolvido por build_question_rectangles.
  Achatar numa faixa de páginas perderia o bbox e o recorte de cada página.
  """

  __tablename__ = "question_pages"
  __table_args__ = (
    UniqueConstraint(
      "question_id", "part_index", name="uq_question_pages_question_id_part_index"
    ),
  )

  id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
  question_id: Mapped[int] = mapped_column(
    ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
  )
  part_index: Mapped[int] = mapped_column(Integer, nullable=False)
  page_no: Mapped[int] = mapped_column(Integer, nullable=False)

  x0: Mapped[float] = mapped_column(Float, nullable=False)
  y0: Mapped[float] = mapped_column(Float, nullable=False)
  x1: Mapped[float] = mapped_column(Float, nullable=False)
  y1: Mapped[float] = mapped_column(Float, nullable=False)

  image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
  text_raw: Mapped[str | None] = mapped_column(Text, nullable=True)

  question: Mapped["Question"] = relationship(back_populates="pages")
