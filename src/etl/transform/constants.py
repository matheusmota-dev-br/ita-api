import re

MAX_QUESTIONS = 60
MARGIN_LEFT = 0
MARGIN_TOP = 12
MARGIN_BOTTOM = 4
OBJECTIVE_BLOCK_END_MARGIN = 6
MIN_NON_QUESTION_REGION_HEIGHT = 20
# Lado mínimo, em pontos, para um recorte virar imagem sem o MuPDF recusar.
MIN_CLIP_SIZE = 2.0
DRAFT_PAGE_MAX_TEXT_LEN = 150
DPI = 150

# Cada alternativa cobre uma forma real observada no corpus:
#   ão      — o caso normal
#   ao      — sem acento
#   a◌̃o     — til combinante (U+0303), do pdflatex
#   ˜ao     — til solto (U+02DC), do pdflatex
#   ion     — o inglês "Question", nas provas de pós
#   [\x80-\x9f]o — o "ã" de um PDF com fonte quebrada, recuperado por
#                  text_repair: o deslocamento acerta o ASCII mas joga os
#                  acentos numa faixa alta. Não casa com texto normal.
_QUESTION_WORD = r"Quest(?:ão|ao|a\u0303o|\u02dcao|ion|[\x80-\x9f]o)"

QUESTION_START_REGEX = re.compile(
  rf"{_QUESTION_WORD}\s+(\d+)\s*[.:]?(?:\s|$)",
  re.IGNORECASE,
)
QUESTION_WORD_ALONE_REGEX = re.compile(rf"{_QUESTION_WORD}\s*$", re.IGNORECASE)
QUESTION_NUMBER_ONLY_LINE_REGEX = re.compile(r"^\s*(\d+)\s*[.:]?(?:\s|$)")

QUESTION_END_REGEX = QUESTION_START_REGEX

QUESTION_IN_TEXT_REGEX = re.compile(
  rf"{_QUESTION_WORD}\s+\d+",
  re.IGNORECASE,
)

OBJECTIVE_OPTION_LETTERS = ("A", "B", "C", "D", "E")
OBJECTIVE_OPTION_LINE_REGEX = re.compile(r"^\s*([A-E])\s*[.)]\s*", re.IGNORECASE)
