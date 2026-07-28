from pathlib import Path

import pymupdf

from src.etl.transform.constants import DPI, MIN_NON_QUESTION_REGION_HEIGHT
from src.etl.transform.intervals import non_question_intervals_on_page
from src.etl.transform.pages import is_draft_page, is_essay_page
from src.etl.transform.paths import (
  get_non_questions_figs_dir,
  get_question_regions_pdf_path,
  get_questions_figs_dir,
)

REGIONS_STROKE_COLOR = (1.0, 0.0, 0.0)
REGIONS_STROKE_WIDTH = 1.5


def export_question_figures(
  doc: pymupdf.Document,
  question_rects: dict[int, list[tuple[int, pymupdf.Rect]]],
  out_dir: Path | None = None,
  dpi: int = DPI,
) -> Path:
  out_dir = out_dir or get_questions_figs_dir()
  out_dir.mkdir(parents=True, exist_ok=True)
  for num, clips in question_rects.items():
    for part, (page_no, clip) in enumerate(clips):
      page = doc[page_no]
      pix = page.get_pixmap(clip=clip, dpi=dpi, alpha=False)
      if len(clips) == 1:
        pix.save(out_dir / f"questao_{num:02d}.png")
      else:
        pix.save(out_dir / f"questao_{num:02d}_p{part + 1}.png")
  return out_dir


def export_non_question_figures(
  doc: pymupdf.Document,
  question_rects: dict[int, list[tuple[int, pymupdf.Rect]]],
  out_dir: Path | None = None,
  dpi: int = DPI,
  min_height: float = MIN_NON_QUESTION_REGION_HEIGHT,
) -> Path:
  out_dir = out_dir or get_non_questions_figs_dir()
  out_dir.mkdir(parents=True, exist_ok=True)
  for page_no in range(len(doc)):
    page = doc[page_no]
    pr = page.rect
    x0, x1 = pr.x0, pr.x1
    intervals = non_question_intervals_on_page(question_rects, page_no, pr)
    for i, (y0, y1) in enumerate(intervals):
      if y1 - y0 < min_height:
        continue
      clip = pymupdf.Rect(x0, y0, x1, y1)
      pix = page.get_pixmap(clip=clip, dpi=dpi, alpha=False)
      if i == 0 and len(intervals) == 1:
        if is_draft_page(page):
          suffix = "rascunho"
        elif is_essay_page(page):
          suffix = "redacao"
        else:
          suffix = "instrucoes_ou_texto_base"
      elif i == 0:
        suffix = "topo_instrucoes"
      elif i == len(intervals) - 1:
        suffix = "rodape"
      else:
        suffix = f"entre_questoes_{i}"
      pix.save(out_dir / f"pagina_{page_no:02d}_{suffix}.png")
  return out_dir


def export_question_regions_pdf(
  doc: pymupdf.Document,
  question_rects: dict[int, list[tuple[int, pymupdf.Rect]]],
  output_path: Path | None = None,
  stroke_color: tuple[float, float, float] = REGIONS_STROKE_COLOR,
  stroke_width: float = REGIONS_STROKE_WIDTH,
) -> Path:
  output_path = output_path or get_question_regions_pdf_path()
  output_path = Path(output_path)
  output_path.parent.mkdir(parents=True, exist_ok=True)
  out_doc = pymupdf.open()
  out_doc.insert_pdf(doc)
  for page_no in range(len(out_doc)):
    page = out_doc[page_no]
    for _num, clips in question_rects.items():
      for pn, clip in clips:
        if pn == page_no:
          page.draw_rect(
            clip,
            color=stroke_color,
            width=stroke_width,
            overlay=True,
          )
          break
  out_doc.save(str(output_path))
  out_doc.close()
  return output_path
