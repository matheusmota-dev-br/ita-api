from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Precisa existir ANTES da primeira migration. Sem convenção de nomes, índices e
# CHECKs saem anônimos e o batch ALTER do Alembic no SQLite não consegue recriá-los.
NAMING_CONVENTION = {
  "ix": "ix_%(table_name)s_%(column_0_N_name)s",
  "uq": "uq_%(table_name)s_%(column_0_N_name)s",
  "ck": "ck_%(table_name)s_%(constraint_name)s",
  "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
  "pk": "pk_%(table_name)s",
}


def _utc_now() -> datetime:
  return datetime.now(timezone.utc)


class Base(DeclarativeBase):
  metadata = MetaData(naming_convention=NAMING_CONVENTION)

  created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
  updated_at: Mapped[datetime] = mapped_column(
    DateTime, default=_utc_now, onupdate=_utc_now
  )
