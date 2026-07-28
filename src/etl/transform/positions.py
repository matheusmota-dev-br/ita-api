import pymupdf

from src.etl.transform.constants import (
  MAX_QUESTIONS,
  QUESTION_NUMBER_ONLY_LINE_REGEX,
  QUESTION_START_REGEX,
  QUESTION_WORD_ALONE_REGEX,
)


def _find_question_positions_in_page(
  page, page_no: int
) -> list[tuple[int, int, pymupdf.Rect]]:
  positions = []
  d = page.get_text("dict", clip=page.rect)
  lines_with_bbox: list[tuple[str, tuple[float, float, float, float]]] = []
  for block in d.get("blocks", []):
    for line in block.get("lines", []):
      line_text = "".join(s.get("text", "") for s in line.get("spans", []))
      bbox = line.get("bbox")
      if bbox is not None:
        lines_with_bbox.append((line_text, bbox))
  for i, (line_text, bbox) in enumerate(lines_with_bbox):
    line_strip = line_text.strip()
    match = QUESTION_START_REGEX.search(line_strip)
    if match:
      n = int(match.group(1))
      positions.append((n, page_no, pymupdf.Rect(bbox)))
    elif QUESTION_WORD_ALONE_REGEX.search(line_strip):
      next_i = i + 1
      if next_i < len(lines_with_bbox):
        next_strip = lines_with_bbox[next_i][0].strip()
        num_match = QUESTION_NUMBER_ONLY_LINE_REGEX.match(next_strip)
        if num_match:
          n = int(num_match.group(1))
          positions.append((n, page_no, pymupdf.Rect(bbox)))
  return positions


def find_question_positions(
  doc, max_questions: int = MAX_QUESTIONS
) -> list[tuple[int, int, pymupdf.Rect]]:
  positions = []
  found_numbers = set()
  for page_no in range(len(doc)):
    page = doc[page_no]
    for n, pno, rect in _find_question_positions_in_page(page, page_no):
      if n not in found_numbers and 1 <= n <= max_questions:
        positions.append((n, pno, rect))
        found_numbers.add(n)
  positions.sort(key=lambda x: (x[1], x[2].y0))
  return positions
