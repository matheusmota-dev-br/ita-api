"""Núcleo do transform: posições, seções, intervalos, páginas e OCR.

Tudo aqui trabalha sobre PDFs reais, porque é o que o código de fato recebe.
`page.get_text("dict")` devolve coordenadas de fonte, e simular isso produziria
um teste que passa sobre uma estrutura que o PyMuPDF nunca gera.
"""

import shutil

import pymupdf
import pytest

from src.constants import Discipline
from src.etl.transform import ocr, pages
from src.etl.transform.intervals import (
  merge_intervals,
  non_question_intervals_on_page,
  question_intervals_on_page,
)
from src.etl.transform.positions import find_question_positions
from src.etl.transform.sections import assign_disciplines, find_section_headers
from src.etl.transform.text_extraction import block_contains_question, get_text_in_rect
from src.etl.transform.text_utils import (
  collapse_whitespace,
  normalize_key,
  strip_accents,
)


@pytest.fixture
def pdf(tmp_path):
  """PDF com texto posicionado por linha, como o detector espera."""

  def _make(pages_text: list[str], name="doc.pdf", width=595, height=842):
    doc = pymupdf.open()
    for body in pages_text:
      page = doc.new_page(width=width, height=height)
      page.insert_textbox(
        pymupdf.Rect(50, 50, width - 50, height - 50), body, fontsize=10, lineheight=1.5
      )
    path = tmp_path / name
    doc.save(str(path))
    doc.close()
    return pymupdf.open(path)

  return _make


# --- posições --------------------------------------------------------------


def _numbers(positions) -> list[int]:
  """`find_question_positions` devolve (numero, pagina, Rect), já ordenado."""
  return [number for number, _page, _rect in positions]


def test_finds_questions_in_order(pdf):
  doc = pdf(["Questão 1. Primeira.\n\nQuestão 2. Segunda.\n\nQuestão 3. Terceira."])
  found = find_question_positions(doc)
  assert _numbers(found) == [1, 2, 3]
  # A ordenação é por (página, y0): a ordem vertical tem que ser crescente.
  ys = [rect.y0 for _n, _p, rect in found]
  assert ys == sorted(ys)
  doc.close()


def test_spans_multiple_pages(pdf):
  doc = pdf(["Questão 1. Na primeira página.", "Questão 2. Na segunda página."])
  by_number = {n: page for n, page, _rect in find_question_positions(doc)}
  assert by_number == {1: 0, 2: 1}
  doc.close()


def test_respects_the_ceiling(pdf):
  doc = pdf(["Questão 1. Uma.\n\nQuestão 2. Duas.\n\nQuestão 3. Tres."])
  assert _numbers(find_question_positions(doc, max_questions=2)) == [1, 2]
  doc.close()


def test_broken_accent_variants_match_the_regex():
  """Os PDFs de 2008–2018 extraem "Questão" como "Quest˜ao".

  Testado contra a string, não contra um PDF gerado: as fontes base-14 do
  PyMuPDF substituem `˜` por `?` ao gravar, então um fixture de PDF verificaria
  um caractere que o arquivo não contém. Estas grafias vêm do corpus real.
  """
  for variant in ["Quest˜ao 1.", "Questao 1.", "Question 1.", "Questão 1."]:
    assert block_contains_question(variant), f"{variant!r} deveria casar"


def test_english_label_matches(pdf):
  doc = pdf(["Question 1. Statement in English."])
  assert _numbers(find_question_positions(doc)) == [1]
  doc.close()


def test_empty_document_finds_nothing(pdf):
  doc = pdf(["Apenas um texto qualquer, sem rótulo de questão."])
  assert find_question_positions(doc) == []
  doc.close()


# --- texto -----------------------------------------------------------------


def test_block_detection_accepts_the_known_variants():
  assert block_contains_question("Questão 12.")
  assert block_contains_question("Quest˜ao 5.")
  assert block_contains_question("Question 3")
  assert not block_contains_question("Questionário de pesquisa")


def test_text_in_rect_returns_only_what_is_inside(pdf):
  doc = pdf(["Questão 1. Dentro do retangulo.\n\n\n\n\n\nMuito abaixo, fora dele."])
  page = doc[0]
  top = get_text_in_rect(page, pymupdf.Rect(0, 0, 595, 90))
  assert "Dentro" in top
  assert "fora dele" not in top
  doc.close()


def test_text_in_rect_is_empty_outside_the_content(pdf):
  doc = pdf(["Questão 1. Texto no topo."])
  assert get_text_in_rect(doc[0], pymupdf.Rect(0, 700, 595, 800)).strip() == ""
  doc.close()


# --- intervalos ------------------------------------------------------------


def test_merge_joins_overlapping_ranges():
  assert merge_intervals([(0, 10), (5, 20), (30, 40)]) == [(0, 20), (30, 40)]


def test_merge_assumes_sorted_input():
  """Ordenar é responsabilidade do chamador.

  `question_intervals_on_page` já devolve ordenado, então `merge_intervals` não
  paga por ordenar de novo. Vale afirmar o contrato para que ninguém o chame
  com entrada solta e conclua que a função está errada.
  """
  assert merge_intervals([(0, 10), (30, 40)]) == [(0, 10), (30, 40)]
  assert merge_intervals([(0, 10), (10, 25)]) == [(0, 25)], "encostados juntam"


def test_merge_of_nothing_is_nothing():
  assert merge_intervals([]) == []


def test_merge_keeps_a_single_range():
  assert merge_intervals([(3, 7)]) == [(3, 7)]


def test_question_intervals_are_complementary_to_the_rest():
  rectangles = {
    1: [(0, pymupdf.Rect(50, 100, 500, 300))],
    2: [(0, pymupdf.Rect(50, 400, 500, 600))],
  }
  inside = question_intervals_on_page(rectangles, 0)
  page_rect = pymupdf.Rect(0, 0, 595, 842)
  outside = non_question_intervals_on_page(rectangles, 0, page_rect)
  assert inside == [(100.0, 300.0), (400.0, 600.0)]
  # Os intervalos de fora não podem se sobrepor aos de dentro.
  for a0, a1 in outside:
    for b0, b1 in inside:
      assert a1 <= b0 or a0 >= b1


def test_intervals_of_a_page_without_questions_cover_everything():
  outside = non_question_intervals_on_page({}, 0, pymupdf.Rect(0, 0, 595, 842))
  assert outside == [(0.0, 842.0)]


# --- classificação de página -----------------------------------------------


def test_page_with_a_question_is_recognised(pdf):
  doc = pdf(["Questão 1. Enunciado."])
  assert pages.page_has_question(doc[0])
  doc.close()


def test_page_without_a_question_is_not(pdf):
  doc = pdf(["Instruções gerais para o candidato."])
  assert not pages.page_has_question(doc[0])
  doc.close()


def test_draft_page_is_detected(pdf):
  doc = pdf(["RASCUNHO"])
  assert pages.is_draft_page(doc[0])
  doc.close()


def test_essay_page_is_detected(pdf):
  doc = pdf(["PROPOSTA DE REDAÇÃO\n\nEscreva um texto dissertativo."])
  assert pages.is_essay_page(doc[0])
  doc.close()


# --- seções ----------------------------------------------------------------


def test_section_headers_are_found_in_order(pdf):
  doc = pdf(
    [
      "MATEMÁTICA\n\nQuestão 1. Uma de matemática.",
      "FÍSICA\n\nQuestão 2. Uma de física.",
    ]
  )
  headers = find_section_headers(doc)
  disciplines = [d for _, _, d in headers]
  assert Discipline.MATEMATICA in disciplines
  assert Discipline.FISICA in disciplines
  doc.close()


def test_disciplines_are_assigned_by_position(pdf):
  doc = pdf(
    [
      "MATEMÁTICA\n\nQuestão 1. Uma de matemática.",
      "FÍSICA\n\nQuestão 2. Uma de física.",
    ]
  )
  positions = find_question_positions(doc)
  assigned = assign_disciplines(doc, positions)
  assert assigned[1][0] is Discipline.MATEMATICA
  assert assigned[2][0] is Discipline.FISICA
  doc.close()


def test_discipline_carries_to_the_next_page_with_lower_confidence(pdf):
  """Uma seção continua na página seguinte, mas com menos certeza."""
  doc = pdf(["QUÍMICA\n\nQuestão 1. Primeira.", "Questão 2. Continua a mesma seção."])
  assigned = assign_disciplines(doc, find_question_positions(doc))
  assert assigned[1][0] is Discipline.QUIMICA
  assert assigned[2][0] is Discipline.QUIMICA
  assert assigned[2][1] < assigned[1][1], "a herdada tem que ter confiança menor"
  doc.close()


def test_no_header_means_no_discipline(pdf):
  doc = pdf(["Questão 1. Sem cabeçalho de matéria."])
  assigned = assign_disciplines(doc, find_question_positions(doc))
  assert assigned.get(1, (None, 0))[0] is None
  doc.close()


# --- utilitários de texto --------------------------------------------------


def test_strip_accents_drops_the_diacritic_but_leaves_a_gap():
  """A extração separa o acento da vogal: "MATEMÁTICA" vira "MATEM´ATICA".

  O NFKD transforma o acento solto em espaço + combinante; o combinante cai e o
  espaço fica. É exatamente por isso que `normalize_key` existe — comparar
  rótulos exige remover o espaço também.
  """
  assert strip_accents("MATEM´ATICA") == "MATEM ATICA"
  assert strip_accents("F˜ISICA") == "F ISICA"
  assert strip_accents("Química") == "Quimica", "acento composto não deixa buraco"


def test_normalize_key_is_case_and_accent_insensitive():
  assert normalize_key("MATEMÁTICA") == normalize_key("matematica")
  assert normalize_key("  Física ") == normalize_key("FISICA")


def test_collapse_whitespace():
  assert collapse_whitespace("a   b\n\nc\t d") == "a b c d"


# --- OCR -------------------------------------------------------------------


def test_install_hint_names_the_package():
  assert "tesseract" in ocr.install_hint().lower()


def test_page_with_text_does_not_need_ocr(pdf):
  doc = pdf(["Questão 1. Tem camada de texto de sobra neste enunciado inteiro."])
  assert not ocr.page_needs_ocr(doc[0])
  doc.close()


def test_page_without_text_needs_ocr(tmp_path):
  doc = pymupdf.open()
  doc.new_page(width=595, height=842)  # página em branco: nenhum caractere
  path = tmp_path / "vazio.pdf"
  doc.save(str(path))
  doc.close()
  reopened = pymupdf.open(path)
  assert ocr.page_needs_ocr(reopened[0])
  assert ocr.document_needs_ocr(reopened)
  reopened.close()


def test_availability_matches_the_binary_on_path():
  """Não afirma que o Tesseract existe — afirma que a detecção concorda."""
  assert ocr.is_available() == (shutil.which("tesseract") is not None)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract ausente")
def test_ocr_reads_a_rasterised_page(tmp_path):
  """Roda só onde há Tesseract; sem ele o pipeline segue e avisa."""
  source = pymupdf.open()
  page = source.new_page(width=400, height=200)
  page.insert_text((30, 100), "Questao 1. Texto rasterizado.", fontsize=18)
  raster = pymupdf.open()
  pix = page.get_pixmap(dpi=200)
  raster.new_page(width=400, height=200).insert_image(
    pymupdf.Rect(0, 0, 400, 200), pixmap=pix
  )
  path = tmp_path / "raster.pdf"
  raster.save(str(path))
  source.close()
  raster.close()

  doc = pymupdf.open(path)
  # A página precisa ficar viva: o TextPage guarda uma referência fraca a ela, e
  # `doc[0]` duas vezes cria dois objetos — o primeiro é coletado e o
  # `get_text(textpage=...)` estoura com ReferenceError.
  page = doc[0]
  textpage = ocr.ocr_textpage(page)
  assert textpage is not None
  text = page.get_text(textpage=textpage)
  assert "uesta" in text.lower() or "questao" in text.lower()
  doc.close()
