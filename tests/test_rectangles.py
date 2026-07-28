"""Construção dos retângulos de recorte."""

import pymupdf

from src.etl.transform.constants import MARGIN_BOTTOM, MARGIN_TOP, MIN_CLIP_SIZE
from src.etl.transform.rectangles import build_question_rectangles, is_renderable


def test_degenerate_clips_are_dropped():
  """Uma questão que termina no topo da página seguinte gera altura zero.

  O MuPDF recusa isso com "Invalid bandwriter header dimensions" e derrubava o
  lote inteiro antes deste filtro.
  """
  assert not is_renderable(pymupdf.Rect(0, 100, 500, 100))
  assert not is_renderable(pymupdf.Rect(0, 100, 500, 100 + MIN_CLIP_SIZE / 2))
  assert not is_renderable(pymupdf.Rect(200, 0, 200, 500))
  assert is_renderable(pymupdf.Rect(0, 100, 500, 400))


def test_uses_each_pages_own_width(make_pdf):
  """Algumas provas de pós têm capa digitalizada de 387x72 seguida de A4.

  Usar as dimensões de uma página fixa recortava o texto de todas as outras.
  """
  doc = pymupdf.open()
  doc.new_page(width=387, height=72)  # capa estreita
  page = doc.new_page(width=595, height=842)
  page.insert_text((56, 100), "Questao 1. Enunciado.", fontsize=11)
  page.insert_text((56, 400), "Questao 2. Enunciado.", fontsize=11)

  positions = [
    (1, 1, pymupdf.Rect(56, 100, 200, 112)),
    (2, 1, pymupdf.Rect(56, 400, 200, 412)),
  ]
  rects = build_question_rectangles(doc, positions)
  doc.close()

  for clips in rects.values():
    for page_no, clip in clips:
      assert clip.x1 == 595, f"largura veio da pagina errada: {clip.x1}"
      assert page_no == 1


def test_consecutive_boxes_overlap_by_exactly_the_margin_difference():
  """As caixas se sobrepõem de propósito, em MARGIN_TOP - MARGIN_BOTTOM.

  A margem superior maior existe para não cortar o cabeçalho "Questão N" no
  topo do recorte; a inferior é menor para não arrastar o começo da questão
  seguinte. A diferença é a folga resultante, e ela precisa ser pequena — uma
  sobreposição grande duplicaria conteúdo entre recortes vizinhos.
  """
  doc = pymupdf.open()
  page = doc.new_page(width=595, height=842)
  page.insert_text((56, 100), "Questao 1. Primeira.", fontsize=11)
  page.insert_text((56, 400), "Questao 2. Segunda.", fontsize=11)

  positions = [
    (1, 0, pymupdf.Rect(56, 100, 200, 112)),
    (2, 0, pymupdf.Rect(56, 400, 200, 412)),
  ]
  rects = build_question_rectangles(doc, positions)
  doc.close()

  _, first = rects[1][0]
  _, second = rects[2][0]
  assert first.y0 == 100 - MARGIN_TOP, "a margem superior nao foi aplicada"
  assert first.y1 == 400 - MARGIN_BOTTOM, "a margem inferior nao foi aplicada"
  assert first.y1 - second.y0 == MARGIN_TOP - MARGIN_BOTTOM
  assert second.y0 < 400, "o cabecalho da questao 2 ficaria cortado"


def test_multipage_question_yields_one_clip_per_page():
  doc = pymupdf.open()
  for _ in range(3):
    doc.new_page(width=595, height=842)

  positions = [
    (1, 0, pymupdf.Rect(56, 700, 200, 712)),
    (2, 2, pymupdf.Rect(56, 100, 200, 112)),
  ]
  rects = build_question_rectangles(doc, positions)
  doc.close()

  pages = [page_no for page_no, _ in rects[1]]
  assert pages == [0, 1, 2], f"esperava 3 pedacos, veio {pages}"


def test_empty_positions_yields_empty():
  doc = pymupdf.open()
  doc.new_page()
  assert build_question_rectangles(doc, []) == {}
  doc.close()
