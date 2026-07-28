from typing import TYPE_CHECKING

from sqlalchemy import (
  Column,
  ForeignKey,
  Integer,
  String,
  Table,
  Text,
  UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base
from src.models.enums import ReviewStatus, enum_col

if TYPE_CHECKING:
  from src.models.asset import Asset
  from src.models.document import Document
  from src.models.question import Question


question_support_texts = Table(
  "question_support_texts",
  Base.metadata,
  Column(
    "question_id",
    ForeignKey("questions.id", ondelete="CASCADE"),
    primary_key=True,
  ),
  Column(
    "support_text_id",
    ForeignKey("support_texts.id", ondelete="CASCADE"),
    primary_key=True,
  ),
)


class SupportText(Base):
  """Bloco de texto que fica fora do retângulo de qualquer questão mas é
  contexto obrigatório para algumas delas.

  Dois casos principais nas provas do ITA: o bloco "Convenções" que abre cada
  seção de matéria definindo notação, e os textos base de Português/Inglês
  compartilhados por um intervalo de questões. `label` reusa os sufixos que
  export_non_question_figures já gera (instrucoes_ou_texto_base, rascunho,
  redacao, topo_instrucoes, rodape, entre_questoes_N).
  """

  __tablename__ = "support_texts"
  __table_args__ = (
    UniqueConstraint(
      "document_id",
      "content_sha256",
      name="uq_support_texts_document_id_content_sha256",
    ),
  )

  id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
  document_id: Mapped[int] = mapped_column(
    ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
  )
  label: Mapped[str | None] = mapped_column(String(64), nullable=True)

  page_start: Mapped[int] = mapped_column(Integer, nullable=False)
  page_end: Mapped[int] = mapped_column(Integer, nullable=False)

  text_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
  text_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
  image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
  content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

  review_status: Mapped[ReviewStatus] = mapped_column(
    enum_col(ReviewStatus), nullable=False, default=ReviewStatus.RAW
  )

  document: Mapped["Document"] = relationship(back_populates="support_texts")
  questions: Mapped[list["Question"]] = relationship(
    secondary=question_support_texts, back_populates="support_texts"
  )
  assets: Mapped[list["Asset"]] = relationship(
    back_populates="support_text", cascade="all, delete-orphan"
  )
