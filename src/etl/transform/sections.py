"""Atribui matéria às questões de uma prova que cobre várias matérias.

As provas de 1ª fase juntam Matemática, Física, Química, Português e Inglês num
PDF só, e a matéria não é impressa em cada questão — ela é dada por um
cabeçalho de seção. O cabeçalho é uma linha curta, em caixa alta, com o nome da
matéria, tipicamente no topo de uma página.

Nem toda prova traz todos os cabeçalhos. Quando uma questão fica antes do
primeiro cabeçalho encontrado, a matéria fica indefinida em vez de ser chutada —
o gabarito e o LLM são as fontes de fallback, e `discipline_source` registra
quem acertou.
"""

import pymupdf

from src.constants import Discipline
from src.etl.transform.text_utils import normalize_key

# Chave normalizada -> matéria. A normalização resolve F´ISICA, INGL ˆES etc.
SECTION_HEADERS: dict[str, Discipline] = {
  "MATEMATICA": Discipline.MATEMATICA,
  "FISICA": Discipline.FISICA,
  "QUIMICA": Discipline.QUIMICA,
  "PORTUGUES": Discipline.PORTUGUES,
  "LINGUAPORTUGUESA": Discipline.PORTUGUES,
  "INGLES": Discipline.INGLES,
  "LINGUAINGLESA": Discipline.INGLES,
}

# Confiança quando a questão vem logo abaixo de um cabeçalho na mesma página.
CONFIDENCE_SAME_PAGE = 1.0
# Confiança quando a matéria foi propagada de um cabeçalho em página anterior.
CONFIDENCE_CARRIED = 0.7

MAX_HEADER_LINE_LENGTH = 40


def find_section_headers(doc: pymupdf.Document) -> list[tuple[int, float, Discipline]]:
  """Cabeçalhos de matéria como (página, y, matéria), em ordem de documento."""
  headers: list[tuple[int, float, Discipline]] = []
  for page_no in range(len(doc)):
    data = doc[page_no].get_text("dict", clip=doc[page_no].rect)
    for block in data.get("blocks", []):
      for line in block.get("lines", []):
        spans = line.get("spans", [])
        text = "".join(s.get("text", "") for s in spans).strip()
        if not text or len(text) > MAX_HEADER_LINE_LENGTH:
          continue
        discipline = SECTION_HEADERS.get(normalize_key(text))
        if discipline is not None:
          headers.append((page_no, line["bbox"][1], discipline))
  headers.sort(key=lambda h: (h[0], h[1]))
  return headers


def assign_disciplines(
  doc: pymupdf.Document,
  positions: list[tuple[int, int, pymupdf.Rect]],
) -> dict[int, tuple[Discipline, float]]:
  """Mapeia número da questão -> (matéria, confiança).

  Questões anteriores ao primeiro cabeçalho ficam de fora do resultado.
  """
  headers = find_section_headers(doc)
  if not headers:
    return {}

  out: dict[int, tuple[Discipline, float]] = {}
  for number, page_no, rect in positions:
    current: tuple[int, float, Discipline] | None = None
    for header in headers:
      if (header[0], header[1]) <= (page_no, rect.y0):
        current = header
      else:
        break
    if current is None:
      continue
    confidence = CONFIDENCE_SAME_PAGE if current[0] == page_no else CONFIDENCE_CARRIED
    out[number] = (current[2], confidence)
  return out
