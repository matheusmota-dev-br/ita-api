"""Separação de enunciado e alternativas a partir do texto bruto.

As entradas são strings reais do corpus, coladas como estão — inclusive os
acentos quebrados que a extração produz nos PDFs de 2008–2018. Testar contra
texto idealizado provaria que o parser funciona num corpus que não existe.
"""

from src.etl.transform.split import split_statement

CHECKBOX = """Questão 1. No circuito representado na figura, têm-se duas lâmpadas
idênticas, L1 e L2, e três fontes idênticas, de mesma tensão V. Então, quando a
chave é fechada,

A ( ) apagam-se as duas lâmpadas.
B ( )  o brilho da L1 aumenta e o da L2 permanece o mesmo.
C ( ) o brilho da L2 aumenta e o da L1 permanece o mesmo.
D ( ) o brilho das duas lâmpadas aumenta.
E ( )  o brilho das duas lâmpadas permanece o mesmo."""

# O caso que fazia o parser achar uma alternativa e perder quatro: quando são
# curtas, a diagramação põe as cinco na mesma linha.
HORIZONTAL = """Questão 2. Sabendo que a massa do planeta é aproximadamente igual
à da Terra, pode-se dizer que a razão entre as massas é de aproximadamente

A ( ) 0,05     B ( ) 0,1     C ( ) 0,6     D ( ) 0,3     E ( ) 4,0"""

LOWER_HALF = """1. What sparked Adrian Newey's early development as a designer
during his childhood?
a) His father's profession as a veterinarian
b) His grandmother's artistic background
c) His fascination with his mother's artistic background
d) Long summer holidays spent sketching car designs
e) His early interest in aerodynamics"""

PAREN = """2. Um paralelepípedo retangular possui arestas de 3, 4 e 12 cm,
pode-se afirmar que este paralelepípedo possuirá

    (a) 16 diagonais de comprimento inteiro
    (b) 12 diagonais de comprimento inteiro
    (c) 8 diagonais de comprimento inteiro
    (d) 4 diagonais de comprimento inteiro
    (e) nenhuma diagonal de comprimento inteiro"""


def test_checkbox_is_the_dominant_style():
  """`A ( )` cobre 54% do corpus — a caixa de marcar do vestibular."""
  split = split_statement(CHECKBOX)
  assert split.style == "checkbox"
  assert split.complete
  assert split.alternatives["A"] == "apagam-se as duas lâmpadas."
  assert split.alternatives["E"] == "o brilho das duas lâmpadas permanece o mesmo."
  assert split.statement.startswith("No circuito")


def test_alternatives_on_a_single_line():
  split = split_statement(HORIZONTAL)
  assert split.complete
  assert list(split.alternatives.values()) == ["0,05", "0,1", "0,6", "0,3", "4,0"]
  assert "0,05" not in split.statement


def test_lowercase_letters_normalise_to_upper():
  split = split_statement(LOWER_HALF)
  assert split.complete
  assert split.alternatives["A"].startswith("His father's")


def test_paren_style():
  split = split_statement(PAREN)
  assert split.style == "paren"
  assert split.complete
  assert split.alternatives["E"] == "nenhuma diagonal de comprimento inteiro"


def test_question_label_is_stripped_from_the_statement():
  """O número já é uma coluna; repeti-lo no enunciado o duplicaria na tela."""
  assert not split_statement(CHECKBOX).statement.startswith("Questão")
  assert not split_statement(LOWER_HALF).statement.startswith("1.")


def test_multiline_alternative_is_joined():
  text = """Enunciado qualquer.

A ( ) primeira parte
      continuação da primeira
B ( ) segunda
C ( ) terceira
D ( ) quarta
E ( ) quinta"""
  expected = "primeira parte continuação da primeira"
  assert split_statement(text).alternatives["A"] == expected


def test_out_of_order_markers_are_treated_as_text():
  """Um "C)" antes do "B)" é numeração dentro do enunciado, não alternativa."""
  text = """Considere os itens abaixo.
C ( ) isto nao abre alternativa
A ( ) primeira
B ( ) segunda"""
  split = split_statement(text)
  assert list(split.alternatives) == ["A", "B"]
  assert "isto nao abre alternativa" in split.statement


def test_unrecognised_text_yields_no_alternatives():
  """Não sugerir é melhor que sugerir errado numa tela de auditoria."""
  split = split_statement("Disserte sobre o papel do vento na erosão costeira.")
  assert split.alternatives == {}
  assert split.style is None
  assert split.statement.startswith("Disserte")


def test_empty_input_is_safe():
  assert split_statement("").alternatives == {}
  assert split_statement("   \n  ").statement == ""


def test_partial_detection_is_reported_as_incomplete():
  """Texto cujo recorte cortou as alternativas não deve parecer bem-sucedido."""
  split = split_statement("Enunciado.\n\nA ( ) unica que sobrou")
  assert split.alternatives == {"A": "unica que sobrou"}
  assert not split.complete


def test_broken_accents_do_not_break_parsing():
  """Os PDFs de 2008–2018 extraem `ã` como `˜a`; a estrutura ainda é legível."""
  text = """3. Sobre as ra´ızes em x da equa¸c˜ao, pode-se aﬁrmar que

    (a) elas ser˜ao sempre iguais
    (b) elas ser˜ao sempre diferentes
    (c) depende do valor de y
    (d) n˜ao existem
    (e) nenhuma das anteriores"""
  split = split_statement(text)
  assert split.complete
  assert split.alternatives["A"] == "elas ser˜ao sempre iguais"


async def test_autofill_endpoint(api):
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = listing["items"][0]["id"]
    body = (await client.get(f"/api/v1/questions/{qid}/autofill")).json()
    # O fixture não tem alternativas no texto bruto: a resposta honesta é
    # devolver o enunciado e nenhuma alternativa.
    assert body["complete"] is False
    assert body["alternatives"] == {}
    assert body["statement"]


async def test_autofill_missing_question(api):
  async with api as client:
    assert (await client.get("/api/v1/questions/9999/autofill")).status_code == 404
