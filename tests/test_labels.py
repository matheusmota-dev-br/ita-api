"""Detecção de início de questão nos dois estilos de rótulo do corpus."""

import pymupdf
import pytest

from src.etl.transform.constants import QUESTION_START_REGEX
from src.etl.transform.labels import LabelStyle, detect_question_positions
from src.etl.transform.text_utils import normalize_key, strip_accents


def numbers(path) -> list[int]:
  doc = pymupdf.open(path)
  try:
    positions, _ = detect_question_positions(doc, max_questions=300)
    return sorted(n for n, _, _ in positions)
  finally:
    doc.close()


def style(path) -> LabelStyle | None:
  doc = pymupdf.open(path)
  try:
    return detect_question_positions(doc, max_questions=300)[1]
  finally:
    doc.close()


def test_labelled_style(make_pdf):
  path = make_pdf(
    [
      "Questao 1. Primeira questao do enunciado.",
      "Questao 2. Segunda questao do enunciado.",
      "Questao 3. Terceira questao do enunciado.",
    ]
  )
  assert numbers(path) == [1, 2, 3]
  assert style(path) is LabelStyle.LABELLED


@pytest.mark.parametrize(
  "line",
  [
    "Questão 1. Enunciado.",
    "Quest˜ao 1. Enunciado.",  # til solto (U+02DC), como sai do pdflatex
    "Questão 1. Enunciado.",  # til combinante (U+0303)
    "Questao 1. Enunciado.",
    "QUESTÃO 1. Enunciado.",
    "Questão 1: Enunciado.",
  ],
)
def test_question_word_variants_match(line):
  """Testado no regex, não via PDF sintético.

  A fixture usa fontes base-14, que não têm o glifo U+02DC e o substituem por
  "·" na hora de gravar — o PDF gerado não conseguiria carregar as variantes
  que aparecem no corpus real. As strings acima foram observadas nos PDFs
  baixados.
  """
  match = QUESTION_START_REGEX.search(line)
  assert match is not None, f"nao casou: {line!r}"
  assert match.group(1) == "1"


def test_question_word_regex_rejects_prose():
  assert QUESTION_START_REGEX.search("responda a questao anterior") is None
  assert QUESTION_START_REGEX.search("Questionario 3") is None


def test_numbered_style(make_pdf):
  path = make_pdf(
    [
      "1. Primeira questao numerada sem a palavra.",
      "2. Segunda questao numerada sem a palavra.",
      "3. Terceira questao numerada sem a palavra.",
      "4. Quarta questao numerada sem a palavra.",
    ]
  )
  assert numbers(path) == [1, 2, 3, 4]
  assert style(path) is LabelStyle.NUMBERED


def test_instruction_list_does_not_win_over_real_questions(make_pdf):
  """A capa das provas de pós tem uma lista numerada de "Observações".

  Uma sequência que começa nela precisa pular as questões reais 1..3 para
  emendar nas seguintes; a que começa na questão 1 real não pula nada. É por
  isso que o critério de seleção conta rejeições, e não páginas cobertas.
  """
  path = make_pdf(
    [
      "Observacoes\n"
      "1. Duracao da prova: 90 minutos.\n"
      "2. Nao e permitido o uso de calculadoras.\n"
      "3. Cada questao admite uma unica resposta.",
      "1. Primeira questao de verdade do exame.",
      "2. Segunda questao de verdade do exame.",
      "3. Terceira questao de verdade do exame.",
      "4. Quarta questao de verdade do exame.",
    ]
  )
  doc = pymupdf.open(path)
  try:
    positions, _ = detect_question_positions(doc, max_questions=300)
    first = min(positions, key=lambda p: (p[1], p[2].y0))
    # A questão 1 escolhida precisa ser a da página 1, não a das instruções.
    assert first[1] == 1, "a lista de instrucoes venceu a selecao de sequencia"
  finally:
    doc.close()


def test_page_numbers_and_captions_are_not_questions(make_pdf):
  """Sem exigir separador e conteúdo na mesma linha, "4 m" e "2" viram questão."""
  path = make_pdf(
    [
      "1. Primeira questao real com corpo de texto.\n4 m\n2 m\n13 de novembro de 2023",
      "2. Segunda questao real com corpo de texto.\n7",
      "3. Terceira questao real com corpo de texto.\n8",
    ]
  )
  assert numbers(path) == [1, 2, 3]


def test_short_numeric_run_is_rejected(make_pdf):
  # Folha de redação com itens numerados não é uma prova.
  path = make_pdf(["1. Leia o texto a seguir.\n2. Escreva uma dissertacao."])
  assert numbers(path) == []
  assert style(path) is None


def test_mixed_styles_merge(make_pdf):
  """Em 2012 a questão 1 vem só como "1." e as demais como "Questao N.".

  A fusão só aceita o candidato numérico se ele cair antes da questão 2 na
  ordem do documento.
  """
  path = make_pdf(
    [
      "1. Ondas acusticas sao ondas de compressao.\n"
      "Questao 2. Considere uma rampa plana inclinada.\n"
      "Questao 3. Um elevador sobe verticalmente.",
    ]
  )
  assert numbers(path) == [1, 2, 3]


def test_accent_normalisation():
  assert normalize_key("F´ISICA") == "FISICA"
  assert normalize_key("INGL ˜ES") == "INGLES"
  assert normalize_key("MATEMÁTICA") == "MATEMATICA"
  assert strip_accents("função") == "funcao"
