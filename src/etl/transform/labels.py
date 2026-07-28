"""Detecção de início de questão, tolerante aos formatos do corpus.

O corpus tem dois estilos de rótulo:

- `Questão 12.` — 95 das 137 provas, incluindo todo o vestibular moderno.
  Alta confiança: a palavra "Questão" não aparece por acaso no começo de uma
  linha. É o que `positions.py` já detectava.
- `12.` sozinho no início da linha — 19 provas, quase todas de pós-graduação,
  mais a primeira questão de alguns PDFs de 2008–2018.

O segundo estilo é ambíguo por natureza: a lista de "Observações" da capa das
provas de pós começa com `1. Duração da prova: 90 minutos`. Por isso os
candidatos numéricos não são aceitos individualmente — eles são encadeados em
sequências crescentes e só a melhor sequência (a que cobre mais páginas) é
aceita. Uma lista de instruções fica confinada a uma página; as questões de uma
prova se espalham por várias.
"""

import re
from dataclasses import dataclass
from enum import Enum

import pymupdf

from src.etl.transform.constants import (
  MAX_QUESTIONS,
  QUESTION_NUMBER_ONLY_LINE_REGEX,
  QUESTION_START_REGEX,
  QUESTION_WORD_ALONE_REGEX,
)
from src.etl.transform.ocr import ocr_textpage, page_needs_ocr
from src.etl.transform.text_repair import Repair, detect_document_repair

# Rótulo autônomo: "12." ou "12)" no início da linha, seguido de conteúdo na
# mesma linha. Os três requisitos são todos necessários — sem o separador
# obrigatório entram legendas de figura ("4 m"), datas ("13 de novembro") e
# números de página soltos; sem exigir conteúdo depois, entra qualquer numeral
# isolado. Note que este regex é mais estrito que QUESTION_NUMBER_ONLY_LINE_REGEX,
# que existe para o caso do número quebrado na linha seguinte a "Questão".
NUMBERED_LABEL_REGEX = re.compile(r"^(\d{1,3})\s*[.)]\s+\S")

# Ao encadear candidatos numéricos, quantos números seguidos podem faltar sem
# que a sequência seja considerada interrompida.
MAX_CHAIN_SKIP = 3

# Sequências numéricas mais curtas que isso são ruído, não uma prova.
MIN_CHAIN_LENGTH = 3


class LabelStyle(str, Enum):
  LABELLED = "labelled"
  NUMBERED = "numbered"


@dataclass(frozen=True)
class Candidate:
  number: int
  page_no: int
  rect: pymupdf.Rect
  style: LabelStyle

  @property
  def order(self) -> tuple[int, float]:
    return (self.page_no, self.rect.y0)


def _page_lines(
  page, repair: Repair | None = None, use_ocr: bool = False
) -> list[tuple[str, tuple[float, float, float, float]]]:
  """Linhas da página com seus bboxes, opcionalmente com o encoding recuperado.

  A recuperação mexe só nos caracteres — as posições vêm corretas mesmo em
  PDFs com a tabela de fontes quebrada, então os retângulos não são afetados.
  """
  lines = []
  textpage = ocr_textpage(page) if use_ocr and page_needs_ocr(page) else None
  data = (
    page.get_text("dict", textpage=textpage)
    if textpage is not None
    else page.get_text("dict", clip=page.rect)
  )
  for block in data.get("blocks", []):
    for line in block.get("lines", []):
      text = "".join(span.get("text", "") for span in line.get("spans", []))
      bbox = line.get("bbox")
      if bbox is not None:
        lines.append((repair.apply(text) if repair else text, bbox))
  return lines


def find_candidates(
  doc: pymupdf.Document,
  max_questions: int = MAX_QUESTIONS,
  use_ocr: bool = False,
) -> list[Candidate]:
  """Todos os candidatos a início de questão, dos dois estilos, sem filtrar."""
  out: list[Candidate] = []
  repair = detect_document_repair(doc)
  for page_no in range(len(doc)):
    lines = _page_lines(doc[page_no], repair, use_ocr)
    for i, (text, bbox) in enumerate(lines):
      stripped = text.strip()

      match = QUESTION_START_REGEX.search(stripped)
      if match:
        number = int(match.group(1))
        if 1 <= number <= max_questions:
          out.append(
            Candidate(number, page_no, pymupdf.Rect(bbox), LabelStyle.LABELLED)
          )
        continue

      # "Questão" sozinho na linha, número quebrado para a linha seguinte.
      if QUESTION_WORD_ALONE_REGEX.search(stripped):
        if i + 1 < len(lines):
          nxt = QUESTION_NUMBER_ONLY_LINE_REGEX.match(lines[i + 1][0].strip())
          if nxt:
            number = int(nxt.group(1))
            if 1 <= number <= max_questions:
              out.append(
                Candidate(number, page_no, pymupdf.Rect(bbox), LabelStyle.LABELLED)
              )
        continue

      numbered = NUMBERED_LABEL_REGEX.match(stripped)
      if numbered:
        number = int(numbered.group(1))
        if 1 <= number <= max_questions:
          out.append(
            Candidate(number, page_no, pymupdf.Rect(bbox), LabelStyle.NUMBERED)
          )
  out.sort(key=lambda c: c.order)
  return out


def _chain_from(candidates: list[Candidate], start: int) -> list[Candidate]:
  """Encadeia candidatos crescentes a partir de `start`, em ordem de documento."""
  chain = [candidates[start]]
  expected = candidates[start].number + 1
  for candidate in candidates[start + 1 :]:
    if candidate.number == expected:
      chain.append(candidate)
      expected += 1
    elif expected < candidate.number <= expected + MAX_CHAIN_SKIP:
      # Tolera uma questão não detectada sem quebrar a sequência.
      chain.append(candidate)
      expected = candidate.number + 1
  return chain


def _score(chain: list[Candidate], universe: list[Candidate]) -> tuple[int, int, int]:
  """Qualidade de uma sequência candidata.

  O critério principal é quantos candidatos a sequência *deixa para trás*
  dentro do próprio intervalo. Uma sequência que começa na lista de
  "Observações" da capa consegue emendar nas questões reais depois, cobrindo
  muitas páginas — mas para isso precisa pular as questões 1..4 verdadeiras,
  que caem dentro do seu intervalo. A sequência que começa na questão 1 real
  não pula nada. Contar páginas não separa as duas; contar rejeições separa.
  """
  if not chain:
    return (-1, -1, -1)
  first, last = chain[0].order, chain[-1].order
  accepted = {id(c) for c in chain}
  rejected = sum(
    1 for c in universe if first <= c.order <= last and id(c) not in accepted
  )
  return (len(chain) - rejected, len({c.page_no for c in chain}), len(chain))


def select_numbered_positions(candidates: list[Candidate]) -> list[Candidate]:
  """Melhor sequência entre os candidatos de rótulo numérico.

  Exige que a sequência comece na questão 1 e tenha ao menos MIN_CHAIN_LENGTH
  itens. Sem isso, os itens numerados de uma folha de redação ou de um bloco de
  instruções viram "questões".
  """
  numbered = [c for c in candidates if c.style is LabelStyle.NUMBERED]
  starts = [i for i, c in enumerate(numbered) if c.number == 1]
  best: list[Candidate] = []
  for start in starts:
    chain = _chain_from(numbered, start)
    if _score(chain, numbered) > _score(best, numbered):
      best = chain
  return best if len(best) >= MIN_CHAIN_LENGTH else []


def _dedupe_keep_first(candidates: list[Candidate]) -> list[Candidate]:
  seen: set[int] = set()
  out = []
  for candidate in candidates:
    if candidate.number not in seen:
      seen.add(candidate.number)
      out.append(candidate)
  return out


def detect_question_positions(
  doc: pymupdf.Document,
  max_questions: int = MAX_QUESTIONS,
  use_ocr: bool = False,
) -> tuple[list[tuple[int, int, pymupdf.Rect]], LabelStyle | None]:
  """Posições de início de questão e o estilo de rótulo que prevaleceu.

  Prefere sempre os rótulos "Questão N", que são inequívocos, e só recorre aos
  numéricos para preencher lacunas ou quando não há nenhum rótulo explícito.
  """
  candidates = find_candidates(doc, max_questions=max_questions, use_ocr=use_ocr)
  labelled = _dedupe_keep_first(
    [c for c in candidates if c.style is LabelStyle.LABELLED]
  )

  if not labelled:
    chain = select_numbered_positions(candidates)
    chain = _dedupe_keep_first(chain)
    style = LabelStyle.NUMBERED if chain else None
    return [(c.number, c.page_no, c.rect) for c in chain], style

  found = {c.number for c in labelled}
  # Preenche lacunas com candidatos numéricos que caiam na posição certa do
  # documento — antes da questão seguinte já encontrada.
  by_number = {c.number: c for c in labelled}
  for candidate in candidates:
    if candidate.style is not LabelStyle.NUMBERED or candidate.number in found:
      continue
    nxt = by_number.get(candidate.number + 1)
    prv = by_number.get(candidate.number - 1)
    if nxt is not None and candidate.order >= nxt.order:
      continue
    if prv is not None and candidate.order <= prv.order:
      continue
    if nxt is None and prv is None:
      continue
    found.add(candidate.number)
    by_number[candidate.number] = candidate

  merged = sorted(by_number.values(), key=lambda c: c.order)
  return [(c.number, c.page_no, c.rect) for c in merged], LabelStyle.LABELLED
