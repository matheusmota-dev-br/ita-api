from typing import TYPE_CHECKING, Any

from sqlalchemy import (
  JSON,
  CheckConstraint,
  ForeignKey,
  Integer,
  String,
  UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base
from src.models.enums import AssetKind, enum_col

if TYPE_CHECKING:
  from src.models.question import Question
  from src.models.support_text import SupportText


class Asset(Base):
  """Um arquivo de imagem produzido pelo transform.

  `relative_path` é sempre relativo a settings.media_root — nunca absoluto. A
  API resolve contra essa raiz e verifica containment antes de servir, porque o
  valor vem do ETL e um manifest ruim viraria path traversal.
  """

  __tablename__ = "assets"
  __table_args__ = (
    UniqueConstraint("relative_path", name="uq_assets_relative_path"),
    # Exatamente um dono.
    CheckConstraint(
      "(question_id IS NOT NULL) <> (support_text_id IS NOT NULL)",
      name="single_owner",
    ),
  )

  id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
  kind: Mapped[AssetKind] = mapped_column(enum_col(AssetKind), nullable=False)

  question_id: Mapped[int | None] = mapped_column(
    ForeignKey("questions.id", ondelete="CASCADE"), nullable=True
  )
  support_text_id: Mapped[int | None] = mapped_column(
    ForeignKey("support_texts.id", ondelete="CASCADE"), nullable=True
  )

  relative_path: Mapped[str] = mapped_column(String(512), nullable=False)
  page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
  bbox: Mapped[Any | None] = mapped_column(JSON, nullable=True)
  dpi: Mapped[int | None] = mapped_column(Integer, nullable=True)
  width_px: Mapped[int | None] = mapped_column(Integer, nullable=True)
  height_px: Mapped[int | None] = mapped_column(Integer, nullable=True)
  sha256: Mapped[str] = mapped_column(String(64), nullable=False)
  byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

  question: Mapped["Question | None"] = relationship(back_populates="assets")
  support_text: Mapped["SupportText | None"] = relationship(back_populates="assets")
