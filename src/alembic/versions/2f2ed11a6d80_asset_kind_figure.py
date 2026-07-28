"""asset kind figure

Acrescenta `figure` aos valores aceitos por `assets.kind`, para anexar a uma
questão uma figura impressa longe dela — o caso das provas de pós, em que a
imagem fica solta numa página adiante.

Escrita à mão: o autogenerate não detecta mudança em CHECK de enum e produziu uma
migration vazia. Sem isto o banco recusaria o insert, porque `enum_col` cria a
constraint de verdade (`create_constraint=True`).

O SQLite não sabe alterar uma CHECK: o `batch_alter_table` recria a tabela, copia
os dados e refaz os índices — daí a necessidade de declarar a coluna inteira.

Revision ID: 2f2ed11a6d80
Revises: 7b6fb8614e22
Create Date: 2026-07-28 13:39:03.788393
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2f2ed11a6d80"
down_revision: str | None = "7b6fb8614e22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Só o sufixo: a naming_convention (`ck_%(table_name)s_%(constraint_name)s`)
# monta o `ck_assets_` na frente. Passar o nome completo gera
# `ck_assets_ck_assets_assetkind` e o drop não encontra nada.
CONSTRAINT = "assetkind"

OLD_VALUES = ("question_crop", "support_crop", "region")
NEW_VALUES = (*OLD_VALUES, "figure")


def _swap(values: tuple[str, ...], length: int) -> None:
  with op.batch_alter_table("assets", schema=None) as batch:
    batch.drop_constraint(CONSTRAINT, type_="check")
    batch.alter_column(
      "kind",
      existing_type=sa.VARCHAR(length=13),
      type_=sa.VARCHAR(length=length),
      existing_nullable=False,
    )
    batch.create_check_constraint(CONSTRAINT, sa.column("kind").in_(values))


def upgrade() -> None:
  # VARCHAR(13) cabe "question_crop"; "figure" é menor, então o tamanho fica.
  _swap(NEW_VALUES, 13)


def downgrade() -> None:
  # Linhas com `figure` violariam a CHECK antiga; virar `region`, que é o valor
  # genérico, é a única descida que não perde a linha.
  op.execute("UPDATE assets SET kind = 'region' WHERE kind = 'figure'")
  _swap(OLD_VALUES, 13)
