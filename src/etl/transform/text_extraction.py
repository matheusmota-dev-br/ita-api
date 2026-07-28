import pymupdf

from src.etl.transform.constants import QUESTION_IN_TEXT_REGEX
from src.etl.transform.ocr import ocr_textpage, page_needs_ocr


def block_contains_question(text: str) -> bool:
  return bool(QUESTION_IN_TEXT_REGEX.search(text))


def get_text_in_rect(page, rect: pymupdf.Rect, textpage=None) -> str:
  if textpage is not None:
    return page.get_text(clip=rect, sort=True, textpage=textpage).strip()
  return page.get_text(clip=rect, sort=True).strip()


class _TextPageCache:
  """Textpages de OCR reaproveitados entre as questões de uma mesma página.

  Sem o cache, uma página com 6 questões passaria pelo Tesseract 6 vezes; a
  300 DPI isso é quase um segundo cada. A referência à página precisa ficar
  viva junto do textpage — o PyMuPDF guarda só uma referência fraca e a
  chamada falha com ReferenceError se a página for coletada antes.
  """

  def __init__(self, doc: pymupdf.Document, enabled: bool) -> None:
    self._doc = doc
    self._enabled = enabled
    self._cache: dict[int, tuple[pymupdf.Page, object] | None] = {}

  def for_page(self, page_no: int):
    if not self._enabled:
      return self._doc[page_no], None
    if page_no not in self._cache:
      page = self._doc[page_no]
      self._cache[page_no] = (
        (page, ocr_textpage(page)) if page_needs_ocr(page) else (page, None)
      )
    return self._cache[page_no]


def extract_question_texts(
  doc: pymupdf.Document,
  question_rects: dict[int, list[tuple[int, pymupdf.Rect]]],
  use_ocr: bool = False,
) -> tuple[dict[int, str], dict[int, str]]:
  """Texto de dentro e de fora do retângulo de cada questão.

  Com `use_ocr`, páginas sem camada de texto passam pelo Tesseract — do
  contrário as questões recortadas de PDFs digitalizados ficariam com
  `statement_raw` vazio e fora do índice de busca.
  """
  cache = _TextPageCache(doc, use_ocr)
  inside: dict[int, str] = {}
  outside: dict[int, str] = {}

  for num, clips in question_rects.items():
    inside_parts = []
    for page_no, clip in clips:
      page, textpage = cache.for_page(page_no)
      inside_parts.append(get_text_in_rect(page, clip, textpage))
    inside[num] = "\n\n".join(t for t in inside_parts if t)

    outside_parts = []
    for page_no, clip in clips:
      page, textpage = cache.for_page(page_no)
      blocks = (
        page.get_text("blocks", textpage=textpage)
        if textpage is not None
        else page.get_text("blocks", clip=page.rect)
      )
      for b in blocks:
        if len(b) < 5:
          continue
        x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
        if not text.strip():
          continue
        if block_contains_question(text):
          continue
        block_rect = pymupdf.Rect(x0, y0, x1, y1)
        if not clip.intersects(block_rect):
          outside_parts.append(f"[Page {page_no + 1}]\n" + text.strip())
    outside[num] = "\n---\n".join(outside_parts) if outside_parts else ""

  return inside, outside
