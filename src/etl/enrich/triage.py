"""Decide quais questões precisam do LLM.

O enrich custa por questão, e 73% do corpus não precisa dele: uma questão de
Inglês ou de Química cujo texto saiu limpo do PDF já está utilizável. O LLM
existe para as que a extração destruiu — matemática virada em `x «6 „ x −2`,
acentos soltos, ou texto vindo de OCR.

Este módulo é o filtro. Ele erra de propósito para o lado de incluir: mandar
uma questão limpa para o LLM desperdiça alguns centavos, deixar uma corrompida
de fora deixa lixo no banco.
"""

import re
from dataclasses import dataclass

# Sinais de matemática corrompida observados no corpus. Cada um veio de um
# caso real: `«` e `„` são pedaços de delimitadores grandes; `˜` e `´` soltos
# são acentos que o pdflatex não combinou; a faixa \x80-\x9f aparece nos PDFs
# com a tabela de fontes quebrada; `: : :` é o que sobra de `\ldots`.
BROKEN_MATH = re.compile(r"[«»„‹›]|[\x80-\x9f]|(?<!\.): : :|˜|(?<=[a-z])´(?=[a-z])|¸")

# Acento separado da vogal, nos dois sentidos.
LOOSE_ACCENT = re.compile(r"[´`˜ˆ¨]\s*[a-zA-Z]|[a-zA-Z]\s*[´`˜ˆ]")

# Ocorrências por mil caracteres. Abaixo disso o texto está utilizável como
# está; o limiar é baixo porque um único `˜` já indica que o PDF quebra acentos
# em algum lugar, e a questão merece uma olhada.
DIRTINESS_THRESHOLD = 2.0


@dataclass(frozen=True)
class Triage:
  dirtiness: float
  from_ocr: bool
  empty: bool

  @property
  def needs_llm(self) -> bool:
    # Texto de OCR sempre precisa: o Tesseract lê `x³` como `x)`.
    # Texto ausente também: só a imagem tem o conteúdo.
    return self.empty or self.from_ocr or self.dirtiness >= DIRTINESS_THRESHOLD

  @property
  def reason(self) -> str:
    if self.empty:
      return "sem texto"
    if self.from_ocr:
      return "texto de OCR"
    if self.dirtiness >= DIRTINESS_THRESHOLD:
      return f"texto corrompido ({self.dirtiness:.1f}/mil)"
    return "texto limpo"


def dirtiness(text: str) -> float:
  """Ocorrências de corrupção por mil caracteres."""
  if not text.strip():
    return 0.0
  hits = len(BROKEN_MATH.findall(text)) + len(LOOSE_ACCENT.findall(text))
  return hits / len(text) * 1000


def triage(text: str, *, from_ocr: bool = False) -> Triage:
  return Triage(
    dirtiness=dirtiness(text),
    from_ocr=from_ocr,
    empty=not text.strip(),
  )
