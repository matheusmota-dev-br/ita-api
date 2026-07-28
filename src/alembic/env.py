import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from src.config import ensure_sqlite_dir, settings
from src.models import Base

config = context.config
if config.config_file_name is not None:
  fileConfig(config.config_file_name)

ensure_sqlite_dir()

config.set_main_option("sqlalchemy.url", settings.database_url_sync)
target_metadata = Base.metadata

# Tabelas gerenciadas por SQL cru (FTS5 e suas shadow tables) ou pelo próprio SQLite.
# Sem este filtro o autogenerate tenta dropá-las a cada migration.
_IGNORED_TABLE_PREFIXES = ("questions_fts", "sqlite_")


def include_object(obj, name, type_, reflected, compare_to) -> bool:
  if type_ == "table" and name.startswith(_IGNORED_TABLE_PREFIXES):
    return False
  return True


def run_migrations_offline() -> None:
  url = config.get_main_option("sqlalchemy.url")
  context.configure(
    url=url,
    target_metadata=target_metadata,
    literal_binds=True,
    dialect_opts={"paramstyle": "named"},
    render_as_batch=True,
    include_object=include_object,
  )
  with context.begin_transaction():
    context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
  # render_as_batch é obrigatório no SQLite: ALTER TABLE nativo não suporta
  # drop/alter de coluna, e o Alembic emula isso recriando a tabela.
  context.configure(
    connection=connection,
    target_metadata=target_metadata,
    render_as_batch=True,
    include_object=include_object,
  )
  with context.begin_transaction():
    context.run_migrations()


async def run_async_migrations() -> None:
  connectable = create_async_engine(
    settings.database_url,
    poolclass=pool.NullPool,
  )
  async with connectable.connect() as connection:
    await connection.run_sync(do_run_migrations)
  await connectable.dispose()


def run_migrations_online() -> None:
  asyncio.run(run_async_migrations())


if context.is_offline_mode():
  run_migrations_offline()
else:
  run_migrations_online()
