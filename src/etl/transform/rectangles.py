import pymupdf

from src.etl.transform.constants import (
  MARGIN_BOTTOM,
  MARGIN_LEFT,
  MARGIN_TOP,
  MIN_CLIP_SIZE,
  OBJECTIVE_BLOCK_END_MARGIN,
  OBJECTIVE_OPTION_LETTERS,
)


def _objective_block_bottom_y(
  page, clip: pymupdf.Rect, margin: float = OBJECTIVE_BLOCK_END_MARGIN
) -> float | None:
  rects = []
  for letter in OBJECTIVE_OPTION_LETTERS:
    rects.extend(page.search_for(f"{letter} (", clip=clip))
    rects.extend(page.search_for(f"{letter} ( )", clip=clip))
  if not rects:
    return None
  return max(r.y1 for r in rects) + margin


def _clip_to_objective_block_end(page, clip: pymupdf.Rect) -> pymupdf.Rect:
  y1_options = _objective_block_bottom_y(page, clip)
  if y1_options is None or y1_options >= clip.y1:
    return clip
  return pymupdf.Rect(clip.x0, clip.y0, clip.x1, y1_options)


def is_renderable(clip: pymupdf.Rect) -> bool:
  """Um recorte precisa ter área para virar imagem.

  Uma questão que termina exatamente no topo da página seguinte produz um
  último pedaço de altura zero; o MuPDF rejeita isso com
  "Invalid bandwriter header dimensions".
  """
  return clip.x1 - clip.x0 >= MIN_CLIP_SIZE and clip.y1 - clip.y0 >= MIN_CLIP_SIZE


def build_question_rectangles(
  doc,
  positions: list[tuple[int, int, pymupdf.Rect]],
) -> dict[int, list[tuple[int, pymupdf.Rect]]]:
  if not positions:
    return {}

  # As páginas de um mesmo PDF podem ter tamanhos diferentes — algumas provas de
  # pós trazem uma capa digitalizada de 387x72pt seguida de páginas A4. Usar as
  # dimensões de uma página fixa recortaria o texto de todas as outras.
  def _bounds(page_no: int) -> tuple[float, float]:
    page_rect = doc[page_no].rect
    return page_rect.x0 + MARGIN_LEFT, page_rect.x1

  questions: dict[int, list[tuple[int, pymupdf.Rect]]] = {}
  for i, (num, page_no, rect) in enumerate(positions):
    start_page = page_no
    start_y = rect.y0 - MARGIN_TOP
    if i + 1 < len(positions):
      _next_num, next_page, next_rect = positions[i + 1]
      end_page = next_page
      end_y = next_rect.y0 - MARGIN_BOTTOM
    else:
      end_page = start_page
      end_y = doc[start_page].rect.y1

    x0_page, x1_page = _bounds(start_page)
    if start_page == end_page:
      clip = pymupdf.Rect(x0_page, start_y, x1_page, end_y)
      clip = _clip_to_objective_block_end(doc[start_page], clip)
      questions[num] = [(start_page, clip)]
    else:
      questions[num] = []
      pr = doc[start_page].rect
      questions[num].append(
        (start_page, pymupdf.Rect(x0_page, start_y, x1_page, pr.y1))
      )
      for p in range(start_page + 1, end_page):
        pr = doc[p].rect
        x0_mid, x1_mid = _bounds(p)
        questions[num].append((p, pymupdf.Rect(x0_mid, pr.y0, x1_mid, pr.y1)))
      pr = doc[end_page].rect
      x0_end, x1_end = _bounds(end_page)
      clip_last = pymupdf.Rect(x0_end, pr.y0, x1_end, end_y)
      clip_last = _clip_to_objective_block_end(doc[end_page], clip_last)
      questions[num].append((end_page, clip_last))

  # Descarta pedaços sem área e questões que ficaram sem nenhum pedaço válido.
  return {
    num: renderable
    for num, clips in questions.items()
    if (renderable := [(p, c) for p, c in clips if is_renderable(c)])
  }
