"""Separa o texto bruto de uma questão em enunciado e alternativas.

Determinístico, sem LLM. O texto extraído do PDF já traz a estrutura — o que
falta é reconhecê-la, e isso é um problema de expressão regular, não de
inferência. Vale a pena porque preencher à mão 2730 questões cujas alternativas
já estão legíveis no texto seria trabalho jogado fora, e porque um parser é
auditável de um jeito que um modelo não é.

Os marcadores foram medidos no corpus, não supostos:

| marcador  | ocorrências | onde                              |
|-----------|-------------|-----------------------------------|
| `A ( )`   | 1476 (54%)  | vestibular — a caixa de marcar    |
| `a)`      |  184        | inglês da pós                     |
| `A)`      |   70        | inglês, outra diagramação         |
| `A.`      |   11        | resíduo                           |
| `(A)`     |    2        | resíduo                           |

Duas decisões que vieram de olhar o texto real:

**As alternativas nem sempre ocupam uma linha cada.** Quando são curtas, o
vestibular as diagrama lado a lado — `A ( ) 0,05   B ( ) 0,1   C ( ) 0,6` numa
linha só. Um parser ancorado em início de linha acha uma e perde quatro.

**A ordem é a defesa contra falso positivo.** Um marcador só abre alternativa se
a letra for exatamente a próxima esperada. Sem isso, um "b)" no meio de uma
frase ou um "3." de uma lista partiriam o enunciado ao meio. É também o que
permite procurar marcadores em qualquer posição da linha sem perder precisão.
"""

import re
from dataclasses import dataclass, field

LETTERS = "ABCDE"

# O corpo de cada estilo, sem âncora: usado tanto no começo da linha quanto no
# meio dela. O grupo 1 é sempre a letra.
_BODIES: list[tuple[str, str, bool]] = [
  # (nome, corpo, pode aparecer no meio da linha)
  #
  # `A ( )` e `(A)` têm delimitador dos dois lados, então são seguros de
  # procurar em qualquer posição. `A)` e `A.` não são: "3." e "b)" aparecem
  # dentro de enunciados o tempo todo, e só a âncora de linha os separa.
  ("checkbox", r"([A-E])[ \t]*\([ \t]*\)", True),
  ("paren", r"\([ \t]*([A-Ea-e])[ \t]*\)", True),
  ("half", r"([A-Ea-e])[ \t]*\)", False),
  ("dot", r"([A-Ea-e])[ \t]*\.", False),
]

# No começo da linha, o marcador precisa ter algo depois — nem que seja o fim
# da linha, no caso de alternativa que só tem figura.
STYLES = {
  name: (re.compile(rf"^[ \t]*{body}(?:[ \t]+(.*)|[ \t]*$)"), inline)
  for name, body, inline in _BODIES
}
# No meio da linha, exige espaço antes para não casar dentro de uma palavra.
INLINE = {
  name: re.compile(rf"(?:(?<=\s)|^)[ \t]*{body}[ \t]*")
  for name, body, inline in _BODIES
  if inline
}


@dataclass
class Split:
  statement: str = ""
  alternatives: dict[str, str] = field(default_factory=dict)
  style: str | None = None

  @property
  def complete(self) -> bool:
    """Todas as cinco alternativas foram encontradas, em ordem."""
    return list(self.alternatives) == list(LETTERS)


def _strip_question_prefix(text: str) -> str:
  """Remove o rótulo "Questão 12." / "12." do começo do enunciado.

  O rótulo é metadado — o número já é uma coluna — e mantê-lo no enunciado o
  faria aparecer duplicado em qualquer renderização.
  """
  return re.sub(
    r"^\s*(?:Quest(?:ão|ao|ion)\s*)?\d{1,3}\s*[.)\-–]\s*",
    "",
    text,
    count=1,
    flags=re.IGNORECASE,
  )


def _detect(lines: list[str]) -> str | None:
  """Escolhe o estilo pela primeira linha que abre a alternativa A.

  Procurar por "A" e não por qualquer letra evita que um "b)" solto no meio de
  um enunciado defina o estilo do resto.
  """
  for name, (pattern, _) in STYLES.items():
    for line in lines:
      match = pattern.match(line)
      if match and match.group(1).upper() == "A":
        return name
  return None


def _markers(line: str, style: str, expected: int) -> list[tuple[str, int, int]]:
  """Marcadores da linha que continuam a sequência A, B, C…

  Devolve (letra, início, fim do marcador). Um marcador fora de ordem é
  ignorado e vira texto da alternativa anterior.
  """
  anchored, allows_inline = STYLES[style]
  found: list[tuple[str, int, int]] = []

  if not allows_inline:
    match = anchored.match(line)
    if match and expected < len(LETTERS):
      letter = match.group(1).upper()
      if letter == LETTERS[expected]:
        # group(2) começa depois do marcador; quando ausente, o fim da linha.
        start = match.start(2) if match.group(2) is not None else len(line)
        found.append((letter, match.start(), start))
    return found

  for match in INLINE[style].finditer(line):
    letter = match.group(1).upper()
    position = expected + len(found)
    if position < len(LETTERS) and letter == LETTERS[position]:
      found.append((letter, match.start(), match.end()))
  return found


def split_statement(raw: str) -> Split:
  """Divide o texto bruto. Devolve `Split` vazio quando não reconhece nada.

  Nunca levanta: um texto que não casa com nenhum estilo devolve o enunciado
  inteiro e nenhuma alternativa, que é a resposta honesta — numa tela de
  auditoria, não sugerir é melhor que sugerir errado.
  """
  if not raw or not raw.strip():
    return Split()

  lines = raw.splitlines()
  style = _detect(lines)
  if style is None:
    return Split(statement=_strip_question_prefix(raw).strip())

  statement: list[str] = []
  alternatives: dict[str, list[str]] = {}
  current: str | None = None
  expected = 0

  for line in lines:
    markers = _markers(line, style, expected)
    if not markers:
      (alternatives[current] if current else statement).append(line)
      continue

    # O texto antes do primeiro marcador pertence ao que vinha antes dele.
    head = line[: markers[0][1]].strip()
    if head:
      (alternatives[current] if current else statement).append(head)

    for index, (letter, _, content_start) in enumerate(markers):
      content_end = markers[index + 1][1] if index + 1 < len(markers) else len(line)
      alternatives[letter] = [line[content_start:content_end]]
      current = letter
    expected += len(markers)

  return Split(
    statement=_strip_question_prefix("\n".join(statement)).strip(),
    alternatives={
      letter: _join(parts) for letter, parts in alternatives.items() if _join(parts)
    },
    style=style,
  )


def _join(parts: list[str]) -> str:
  """Junta as linhas de uma alternativa, colapsando as quebras do PDF.

  A quebra de linha dentro de uma alternativa é da diagramação, não do texto;
  preservá-la deixaria o campo com quebras arbitrárias no meio de frases.
  """
  return re.sub(r"\s+", " ", " ".join(parts)).strip()
