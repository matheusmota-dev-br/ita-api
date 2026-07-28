"""Recuperação de texto de PDFs com tabela de codificação quebrada.

Alguns PDFs do corpus embutem fontes sem `ToUnicode` válido. O PyMuPDF então
devolve os códigos de glifo crus em vez dos caracteres, e o texto sai como
`&RQVWDQWH GH $YRJDGUR` no lugar de `Constante de Avogadro`.

Quando o mapeamento é um deslocamento uniforme — o caso da prova de química de
2018, deslocada em 29 — dá para desfazer somando a diferença de volta. As
posições dos spans continuam corretas o tempo todo; só os caracteres estão
errados, então a recuperação não afeta os retângulos de recorte.

Nem toda quebra é um deslocamento uniforme: as provas de inglês da pós de 2024
em diante usam índices de glifo sem relação linear com Unicode, e para essas
não há o que fazer aqui — vão para o fallback de visão.
"""

import re
from dataclasses import dataclass

# Caracteres de controle que não deveriam aparecer em texto extraído.
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Deslocamentos plausíveis. O caso conhecido é 29; a varredura cobre a vizinhança
# sem custo relevante, já que roda uma vez por documento.
CANDIDATE_SHIFTS = range(1, 96)

# Abaixo desta fração de caracteres de controle o texto é considerado saudável.
BROKEN_THRESHOLD = 0.02
# Uma recuperação só é aceita se melhorar a pontuação neste fator...
MIN_IMPROVEMENT = 1.5
# ...e se o resultado for absolutamente legível. Sem este piso, texto que é
# só bytes de controle tem pontuação inicial ~0, e qualquer deslocamento que
# caia na faixa ASCII 'melhora' 1,5x — aceitando lixo como recuperação e,
# pior, impedindo o OCR de assumir o caso.
MIN_ABSOLUTE_SCORE = 0.60

# Prosa real separa palavras: entre 8% e 30% dos caracteres são espaços.
# Um deslocamento que mapeie bytes baixos para letras produz uma corrida
# contínua sem espaço nenhum — densidade de letras 1,0 e zero significado.
MIN_SPACE_RATIO = 0.08
MAX_SPACE_RATIO = 0.30

_COMMON = (
  " de ",
  " da ",
  " do ",
  " que ",
  " para ",
  " com ",
  " em ",
  " os ",
  " as ",
  " the ",
  " of ",
  " and ",
  " to ",
)


def is_broken(text: str) -> bool:
  if not text:
    return False
  return len(CONTROL_CHARS.findall(text)) / len(text) > BROKEN_THRESHOLD


def _score(text: str) -> float:
  """Quão parecido com português ou inglês legível o texto está.

  Combina duas evidências: densidade de caracteres imprimíveis comuns e a
  presença de palavras funcionais curtas. Só a primeira aceitaria qualquer
  deslocamento que caia na faixa ASCII; só a segunda é frágil em textos curtos.
  """
  if not text:
    return 0.0
  # Conta letras e espaços, não "caracteres imprimíveis": um deslocamento que
  # produz `)*+)),-.))/.01))` é 94% imprimível e 0% texto. Prosa real fica
  # entre 0,70 e 0,85 nesta métrica.
  spaces = sum(1 for c in text if c == " ")
  space_ratio = spaces / len(text)
  if not MIN_SPACE_RATIO <= space_ratio <= MAX_SPACE_RATIO:
    return 0.0
  letters = sum(1 for c in text if c.isalpha() or c.isspace())
  density = letters / len(text)
  lowered = f" {text.lower()} "
  words = sum(lowered.count(w) for w in _COMMON)
  return density + min(words, 20) * 0.05


@dataclass(frozen=True)
class Repair:
  shift: int
  score_before: float
  score_after: float

  def apply(self, text: str) -> str:
    return shift_text(text, self.shift)


def shift_text(text: str, shift: int) -> str:
  out = []
  for char in text:
    code = ord(char) + shift
    # Só desloca o que estava na faixa quebrada; quebras de linha e caracteres
    # já legíveis passam intactos.
    out.append(chr(code) if 0 <= ord(char) < 0x2000 and 32 <= code < 0x2000 else char)
  return "".join(out)


def detect_repair(text: str) -> Repair | None:
  """Encontra o deslocamento que torna o texto legível, se houver algum."""
  if not is_broken(text):
    return None

  sample = text[:4000]
  before = _score(sample)
  best: Repair | None = None
  for shift in CANDIDATE_SHIFTS:
    after = _score(shift_text(sample, shift))
    if (
      after > before * MIN_IMPROVEMENT
      and after >= MIN_ABSOLUTE_SCORE
      and (best is None or after > best.score_after)
    ):
      best = Repair(shift=shift, score_before=before, score_after=after)
  return best


def detect_document_repair(doc) -> Repair | None:
  """Detecta o deslocamento uma vez, sobre o texto do documento inteiro."""
  text = "".join(doc[i].get_text() for i in range(min(len(doc), 5)))
  return detect_repair(text)
