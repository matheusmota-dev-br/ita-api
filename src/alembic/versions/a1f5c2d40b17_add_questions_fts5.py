"""add questions_fts5

Revision ID: a1f5c2d40b17
Revises: cf3035360231
Create Date: 2026-07-27

Índice full-text sobre questions.search_blob.

Escrito à mão porque o SQLAlchemy declarativo não expressa tabela virtual. Duas
escolhas deliberadas:

- `content='questions'` (external content): o índice guarda só a lista invertida,
  não uma segunda cópia do texto. Reduz pela metade o tamanho do .db.
- o trigger de update é `AFTER UPDATE OF search_blob`, não `AFTER UPDATE`: o
  loader toca `updated_at` em todo upsert, e um trigger genérico reindexaria o
  corpus inteiro a cada execução do ETL.

`remove_diacritics 2` é o que faz "funcao" casar com "função" — sem isso a busca
em português é inútil.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1f5c2d40b17"
down_revision: str | None = "cf3035360231"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRIGGERS = ("questions_fts_ai", "questions_fts_ad", "questions_fts_au")


def _require_fts5(bind) -> None:
  enabled = bind.exec_driver_sql(
    "SELECT count(*) FROM pragma_compile_options() "
    "WHERE compile_options = 'ENABLE_FTS5'"
  ).scalar()
  if not enabled:
    raise RuntimeError(
      "Este SQLite foi compilado sem FTS5. Instale um Python cujo sqlite3 "
      "tenha ENABLE_FTS5, ou rode com SEARCH_BACKEND=like."
    )


def upgrade() -> None:
  bind = op.get_bind()
  if bind.dialect.name != "sqlite":
    return
  _require_fts5(bind)

  op.execute("""
    CREATE VIRTUAL TABLE questions_fts USING fts5(
      search_blob,
      content='questions',
      content_rowid='id',
      tokenize="unicode61 remove_diacritics 2"
    );
  """)
  op.execute("""
    CREATE TRIGGER questions_fts_ai AFTER INSERT ON questions BEGIN
      INSERT INTO questions_fts(rowid, search_blob)
      VALUES (new.id, new.search_blob);
    END;
  """)
  op.execute("""
    CREATE TRIGGER questions_fts_ad AFTER DELETE ON questions BEGIN
      INSERT INTO questions_fts(questions_fts, rowid, search_blob)
      VALUES ('delete', old.id, old.search_blob);
    END;
  """)
  op.execute("""
    CREATE TRIGGER questions_fts_au AFTER UPDATE OF search_blob ON questions BEGIN
      INSERT INTO questions_fts(questions_fts, rowid, search_blob)
      VALUES ('delete', old.id, old.search_blob);
      INSERT INTO questions_fts(rowid, search_blob)
      VALUES (new.id, new.search_blob);
    END;
  """)


def downgrade() -> None:
  bind = op.get_bind()
  if bind.dialect.name != "sqlite":
    return
  for trigger in _TRIGGERS:
    op.execute(f"DROP TRIGGER IF EXISTS {trigger};")
  op.execute("DROP TABLE IF EXISTS questions_fts;")
