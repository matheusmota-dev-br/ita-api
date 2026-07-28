"""Busca full-text sobre questions_fts."""

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings

_TOKEN_SPLIT = re.compile(r"\W+", re.UNICODE)


def to_fts_query(raw: str) -> str:
  """Converte texto livre em uma expressão MATCH válida.

  Obrigatório: o input do usuário chega direto na *sintaxe* do FTS5, então algo
  como `a"b` ou `foo(` levantaria OperationalError. Cada termo vira uma frase
  entre aspas e todos são combinados com AND.
  """
  terms = [t for t in _TOKEN_SPLIT.split(raw) if t]
  return " AND ".join(f'"{t}"' for t in terms)


async def fts_available(session: AsyncSession) -> bool:
  if settings.search_backend != "fts5":
    return False
  result = await session.execute(
    text(
      "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='questions_fts'"
    )
  )
  return bool(result.scalar())


async def search_question_ids(
  session: AsyncSession, query: str
) -> list[tuple[int, str]]:
  """IDs de questões que casam com a busca, já ordenados por relevância.

  Devolve (id, trecho destacado). Um LIKE seria uma varredura completa e não
  casaria "funcao" com "função"; o FTS5 resolve os dois com o tokenizer
  unicode61 remove_diacritics.
  """
  match = to_fts_query(query)
  if not match:
    return []

  if not await fts_available(session):
    like = f"%{query}%"
    result = await session.execute(
      text(
        "SELECT id, substr(search_blob, 1, 240) FROM questions "
        "WHERE search_blob LIKE :like"
      ),
      {"like": like},
    )
    return [(row[0], row[1] or "") for row in result]

  result = await session.execute(
    text("""
      SELECT questions_fts.rowid,
             snippet(questions_fts, 0, '<mark>', '</mark>', '…', 24)
      FROM questions_fts
      WHERE questions_fts MATCH :match
      ORDER BY bm25(questions_fts)
    """),
    {"match": match},
  )
  return [(row[0], row[1] or "") for row in result]
