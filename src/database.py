from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import ensure_sqlite_dir, settings


def apply_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
  """No SQLite as foreign keys vêm DESLIGADAS por conexão.

  Sem isso todo ON DELETE CASCADE do schema é decorativo.
  """
  cursor = dbapi_connection.cursor()
  cursor.execute("PRAGMA foreign_keys=ON")
  cursor.execute("PRAGMA busy_timeout=5000")
  cursor.close()


ensure_sqlite_dir()

# pool_pre_ping não faz sentido para um arquivo local — não há conexão que possa cair.
engine = create_async_engine(settings.database_url, echo=False)

if engine.dialect.name == "sqlite":
  event.listen(engine.sync_engine, "connect", apply_sqlite_pragmas)

async_session_maker = async_sessionmaker(
  engine,
  class_=AsyncSession,
  expire_on_commit=False,
  autocommit=False,
  autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
  async with async_session_maker() as session:
    try:
      yield session
      await session.commit()
    except SQLAlchemyError:
      await session.rollback()
      raise
    finally:
      await session.close()


async def check_db_connection() -> bool:
  try:
    async with async_session_maker() as session:
      await session.execute(text("SELECT 1"))
    return True
  except SQLAlchemyError:
    return False
