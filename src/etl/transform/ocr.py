"""OCR para os PDFs do corpus que não têm camada de texto.

Onze documentos — a prova de 2026 inteira e algumas de inglês da pós — são
digitalizações puras: `page.get_text()` devolve string vazia, então nenhuma
heurística de rótulo tem em que trabalhar.

O OCR é preferível ao fallback de visão por LLM para este problema específico,
porque devolve **texto com coordenadas**. O pipeline precisa saber onde cada
questão começa na página, não só que ela existe; um modelo de visão descreve o
conteúdo, mas estimar o `y0` de um cabeçalho por inspeção visual é impreciso.

Depende do Tesseract instalado no sistema (`brew install tesseract
tesseract-lang`). Sem ele, `is_available()` devolve False e o pipeline segue
sem OCR em vez de quebrar.
"""

from functools import lru_cache

import pymupdf

from src.etl.transform.text_repair import detect_repair, is_broken

# Resolução do OCR. 300 é o ponto em que o Tesseract lê texto de corpo com
# confiança; abaixo disso a taxa de erro em acentos sobe bastante.
OCR_DPI = 300

# Idiomas: as provas são em português, as de inglês da pós são em inglês.
OCR_LANGUAGE = "por+eng"

# Uma página com menos texto que isto continua sendo tratada como vazia.
MIN_OCR_CHARS = 40


@lru_cache(maxsize=1)
def is_available() -> bool:
  """Testa o OCR de verdade, numa página em branco descartável.

  Checar apenas se o binário existe não basta: o PyMuPDF também precisa
  encontrar o `tessdata`, e a falha por falta dele só aparece na chamada.
  """
  doc = pymupdf.open()
  try:
    page = doc.new_page(width=100, height=100)
    page.get_textpage_ocr(dpi=72, full=True)
    return True
  except (RuntimeError, pymupdf.mupdf.FzErrorBase):
    return False
  finally:
    doc.close()


def page_needs_ocr(page) -> bool:
  """Página sem texto útil: em branco, ou com um encoding que não dá para
  recuperar por deslocamento (as provas de inglês da pós de 2024 em diante
  usam índices de glifo sem relação linear com Unicode)."""
  text = page.get_text().strip()
  if len(text) < MIN_OCR_CHARS:
    return True
  return is_broken(text) and detect_repair(text) is None


def document_needs_ocr(doc: pymupdf.Document) -> bool:
  """Verdadeiro se ao menos uma página não tem texto aproveitável.

  Exigir que a *maioria* das páginas esteja em branco deixava de fora as provas
  parcialmente digitalizadas — metade escaneada, metade com texto. Como a
  retentativa com OCR só roda depois de a detecção normal falhar, o critério
  generoso não custa nada nos documentos que já funcionam.
  """
  return any(page_needs_ocr(doc[i]) for i in range(len(doc)))


def ocr_textpage(page, dpi: int = OCR_DPI, language: str = OCR_LANGUAGE):
  """TextPage obtido por OCR, com as coordenadas na escala da página.

  Devolve None quando o OCR não está disponível ou falha nesta página, para que
  o chamador possa seguir com o que tiver.
  """
  if not is_available():
    return None
  try:
    return page.get_textpage_ocr(dpi=dpi, language=language, full=True)
  except (RuntimeError, pymupdf.mupdf.FzErrorBase):
    return None


def install_hint() -> str:
  return (
    "OCR indisponivel: instale o Tesseract com "
    "`brew install tesseract tesseract-lang` (macOS) ou "
    "`apt-get install tesseract-ocr tesseract-ocr-por` (Debian/Ubuntu)"
  )
