"""Parser dos PDFs de gabarito.

Todos os gabaritos do corpus são a mesma coisa visualmente: uma tabela com uma
coluna por matéria, cada coluna alternando número da questão e letra da
resposta. O que muda entre as eras é só a numeração — de 2019 em diante ela é
global na prova (1..70), e em 2008–2018 cada matéria recomeça em 1.

Por isso o parse é geométrico, não textual: as células são agrupadas em colunas
por coordenada x e cada coluna é associada ao cabeçalho de matéria acima dela.
Sem isso, `1 B` da Física e `1 D` da Matemática seriam indistinguíveis na era
antiga. De quebra, o gabarito vira uma fonte de matéria para as questões de 1ª
fase que o cabeçalho de seção não cobriu.
"""

import re
from dataclasses import dataclass

import pymupdf

from src.constants import Discipline
from src.etl.transform.text_utils import normalize_key

LETTER_REGEX = re.compile(r"^([A-E])$")
NUMBER_REGEX = re.compile(r"^(\d{1,3})$")
# (*) e (**) nos gabaritos modernos, * sozinho nos antigos.
ANNULLED_REGEX = re.compile(r"^\(?\*+\)?$")

DISCIPLINE_HEADERS = {
  "MATEMATICA": Discipline.MATEMATICA,
  "FISICA": Discipline.FISICA,
  "QUIMICA": Discipline.QUIMICA,
  "PORTUGUES": Discipline.PORTUGUES,
  "INGLES": Discipline.INGLES,
  "REDACAO": Discipline.REDACAO,
}

# Distância vertical máxima, em pontos, entre o número e a sua letra.
MAX_PAIR_DY = 6.0
# Distância horizontal máxima entre o número e a letra da mesma célula.
MAX_PAIR_DX = 120.0
# Coordenadas x dentro desta distância pertencem à mesma coluna da tabela.
COLUMN_TOLERANCE = 25.0
# Fração da coluna mediana abaixo da qual a "coluna" é ruído, não uma matéria.
MIN_COLUMN_DENSITY = 0.5


@dataclass(frozen=True)
class AnswerEntry:
  number: int
  letter: str | None
  annulled: bool
  discipline: Discipline | None
  # Índice da coluna da tabela, da esquerda para a direita. Preserva a
  # identidade da matéria mesmo quando o cabeçalho não pôde ser casado — sem
  # isso, as cinco questões "1" de 2008 seriam a mesma.
  column: int
  raw: str


@dataclass
class _Word:
  text: str
  x0: float
  y0: float
  x1: float


def _words(page) -> list[_Word]:
  return [
    _Word(text=w[4].strip(), x0=w[0], y0=w[1], x1=w[2])
    for w in page.get_text("words")
    if w[4].strip()
  ]


def _find_discipline_columns(words: list[_Word]) -> list[tuple[float, Discipline]]:
  """Cabeçalhos de matéria como (x, matéria), ordenados da esquerda.

  Uma mesma matéria pode aparecer mais de uma vez na página (num título e no
  cabeçalho da coluna); vale a ocorrência mais à esquerda.
  """
  leftmost: dict[Discipline, float] = {}
  for word in words:
    discipline = DISCIPLINE_HEADERS.get(normalize_key(word.text))
    if discipline is None:
      continue
    x = (word.x0 + word.x1) / 2
    if discipline not in leftmost or x < leftmost[discipline]:
      leftmost[discipline] = x
  return sorted(((x, d) for d, x in leftmost.items()), key=lambda c: c[0])


def _cluster_columns(
  xs: list[float], tolerance: float = COLUMN_TOLERANCE
) -> list[float]:
  """Agrupa coordenadas x em colunas, devolvendo o centro de cada uma."""
  clusters: list[list[float]] = []
  for x in sorted(xs):
    if clusters and x - clusters[-1][-1] <= tolerance:
      clusters[-1].append(x)
    else:
      clusters.append([x])
  return [sum(c) / len(c) for c in clusters]


def _column_counts(number_xs: list[float], columns: list[float]) -> list[int]:
  counts = [0] * len(columns)
  for x in number_xs:
    counts[_column_index(x, columns)] += 1
  return counts


def _map_columns_to_disciplines(
  columns: list[float],
  headers: list[tuple[float, Discipline]],
  counts: list[int],
) -> dict[int, Discipline] | None:
  """Casa colunas de dados com cabeçalhos, da esquerda para a direita.

  Colunas ralas são descartadas antes do casamento: alguns gabaritos têm uma
  célula solta que forma uma "coluna" de uma entrada só e desalinharia todo o
  resto. Se depois disso a contagem ainda não bater, devolve None — associar
  pelo cabeçalho mais próximo parece natural mas erra feio quando um cabeçalho
  está deslocado, e em 2010/2011 rotulava a coluna de Português como Inglês.
  Sem rótulo, o índice da coluna ainda distingue as matérias.
  """
  if not columns or not headers:
    return None

  median = sorted(counts)[len(counts) // 2]
  dense = [i for i, c in enumerate(counts) if c >= median * MIN_COLUMN_DENSITY]
  if len(dense) != len(headers):
    return None
  return {column: headers[position][1] for position, column in enumerate(dense)}


def _column_index(x: float, columns: list[float]) -> int:
  return min(range(len(columns)), key=lambda i: abs(columns[i] - x))


def parse_answer_key(doc: pymupdf.Document) -> list[AnswerEntry]:
  """Todas as respostas de um PDF de gabarito, na ordem em que aparecem."""
  entries: list[AnswerEntry] = []
  seen: set[tuple[Discipline | None, int]] = set()

  for page_no in range(len(doc)):
    words = _words(doc[page_no])
    headers = _find_discipline_columns(words)

    # Primeira passada: localiza os pares (número, letra) e suas posições.
    pairs: list[tuple[_Word, _Word, int, str | None, bool]] = []
    for i, word in enumerate(words):
      number_match = NUMBER_REGEX.match(word.text)
      if not number_match:
        continue

      # A resposta é a próxima palavra à direita, na mesma linha.
      answer: _Word | None = None
      for candidate in words[i + 1 :]:
        if abs(candidate.y0 - word.y0) > MAX_PAIR_DY:
          continue
        if candidate.x0 < word.x1 or candidate.x0 - word.x1 > MAX_PAIR_DX:
          continue
        answer = candidate
        break

      if answer is None:
        continue

      letter_match = LETTER_REGEX.match(answer.text.upper())
      annulled = bool(ANNULLED_REGEX.match(answer.text))
      if not letter_match and not annulled:
        continue

      pairs.append(
        (
          word,
          answer,
          int(number_match.group(1)),
          letter_match.group(1) if letter_match else None,
          annulled,
        )
      )

    # Segunda passada: as colunas só podem ser identificadas depois de conhecer
    # todas as posições. Agrupa pela borda esquerda, que é o que fica alinhado
    # numa tabela — o centro varia conforme o número tenha um ou dois dígitos.
    number_xs = [w.x0 for w, _, _, _, _ in pairs]
    columns = _cluster_columns(number_xs)
    mapping = _map_columns_to_disciplines(
      columns, headers, counts=_column_counts(number_xs, columns)
    )

    for word, answer, number, letter, annulled in pairs:
      column = _column_index(word.x0, columns)
      discipline = mapping.get(column) if mapping else None

      key = (page_no, column, number)
      if key in seen:
        continue
      seen.add(key)

      entries.append(
        AnswerEntry(
          number=number,
          letter=letter,
          annulled=annulled,
          discipline=discipline,
          column=column,
          raw=f"{word.text} {answer.text}",
        )
      )

  entries.sort(key=lambda e: (e.discipline.value if e.discipline else "", e.number))
  return entries
