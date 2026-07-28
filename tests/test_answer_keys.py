"""Parser dos gabaritos, que é geométrico e não textual."""

import pymupdf

from src.constants import Discipline
from src.etl.transform.answer_keys import parse_answer_key


def build_key_pdf(columns: list[tuple[str, list[tuple[int, str]]]], width=595):
  """Monta um gabarito em colunas: [(cabecalho, [(numero, letra), ...]), ...]."""
  doc = pymupdf.open()
  page = doc.new_page(width=width, height=842)
  step = width // (len(columns) + 1)
  for index, (header, entries) in enumerate(columns):
    x = 40 + index * step
    page.insert_text((x, 80), header, fontsize=11)
    for row, (number, letter) in enumerate(entries):
      y = 110 + row * 20
      page.insert_text((x, y), str(number), fontsize=11)
      page.insert_text((x + 40, y), letter, fontsize=11)
  return doc


def test_parses_letters_and_disciplines():
  doc = build_key_pdf(
    [
      ("MATEMATICA", [(1, "B"), (2, "E"), (3, "C")]),
      ("FISICA", [(4, "D"), (5, "A"), (6, "B")]),
    ]
  )
  entries = parse_answer_key(doc)
  doc.close()

  by_number = {e.number: e for e in entries}
  assert by_number[1].letter == "B"
  assert by_number[1].discipline is Discipline.MATEMATICA
  assert by_number[5].letter == "A"
  assert by_number[5].discipline is Discipline.FISICA
  assert len(entries) == 6


def test_repeated_numbers_across_columns_are_distinct():
  """Na era 2008–2018 cada matéria recomeça a numeração em 1.

  Deduplicar só por número colapsaria as cinco questões "1" numa só — foi
  exatamente essa regressão que o índice de coluna corrigiu.
  """
  doc = build_key_pdf(
    [
      ("MATEMATICA", [(1, "A"), (2, "B")]),
      ("FISICA", [(1, "C"), (2, "D")]),
      ("QUIMICA", [(1, "E"), (2, "A")]),
    ]
  )
  entries = parse_answer_key(doc)
  doc.close()

  assert len(entries) == 6, f"perdeu respostas: {len(entries)}"
  ones = {e.discipline: e.letter for e in entries if e.number == 1}
  assert ones == {
    Discipline.MATEMATICA: "A",
    Discipline.FISICA: "C",
    Discipline.QUIMICA: "E",
  }


def test_annulled_questions():
  doc = build_key_pdf([("MATEMATICA", [(1, "B"), (2, "(*)"), (3, "C")])])
  entries = parse_answer_key(doc)
  doc.close()

  annulled = [e for e in entries if e.annulled]
  assert len(annulled) == 1
  assert annulled[0].number == 2
  assert annulled[0].letter is None


def test_column_identity_survives_missing_headers():
  """Sem cabeçalhos reconhecíveis a matéria fica indefinida — mas as respostas
  continuam distintas por coluna, em vez de serem perdidas."""
  doc = build_key_pdf([("XXXX", [(1, "A"), (2, "B")]), ("YYYY", [(1, "C"), (2, "D")])])
  entries = parse_answer_key(doc)
  doc.close()

  assert len(entries) == 4
  assert all(e.discipline is None for e in entries)
  assert len({e.column for e in entries}) == 2


def test_non_answer_text_is_ignored():
  doc = pymupdf.open()
  page = doc.new_page(width=595, height=842)
  page.insert_text((40, 60), "INSTITUTO TECNOLOGICO DE AERONAUTICA", fontsize=11)
  page.insert_text((40, 80), "VESTIBULAR 2025", fontsize=11)
  page.insert_text((40, 100), "MATEMATICA", fontsize=11)
  for row, (number, letter) in enumerate([(1, "B"), (2, "E")]):
    page.insert_text((40, 130 + row * 20), str(number), fontsize=11)
    page.insert_text((80, 130 + row * 20), letter, fontsize=11)
  entries = parse_answer_key(doc)
  doc.close()

  assert sorted(e.number for e in entries) == [1, 2]
