"""Normalização de texto extraído de PDFs gerados por LaTeX.

As provas do ITA saem do pdflatex com acentos decompostos de formas que não
sobrevivem à extração: `Questão` vira `Quest˜ao`, `FÍSICA` vira `F´ISICA`,
`INGLÊS` vira `INGL ˆES`. Comparar strings sem passar por aqui não funciona.
"""

import re
import unicodedata

# Modificadores soltos que o pdflatex emite como caractere próprio em vez de
# combinar com a vogal seguinte.
_LOOSE_DIACRITICS = re.compile(r"[ˆ˜´`¨ˇ˚]")
_WHITESPACE = re.compile(r"\s+")


def strip_accents(text: str) -> str:
  """Remove acentos, combinantes e modificadores soltos."""
  decomposed = unicodedata.normalize("NFKD", text)
  without_combining = "".join(c for c in decomposed if not unicodedata.combining(c))
  return _LOOSE_DIACRITICS.sub("", without_combining)


def normalize_key(text: str) -> str:
  """Chave canônica para comparar rótulos: sem acento, sem espaço, maiúsculo."""
  return _WHITESPACE.sub("", strip_accents(text)).upper()


def collapse_whitespace(text: str) -> str:
  return _WHITESPACE.sub(" ", text).strip()
