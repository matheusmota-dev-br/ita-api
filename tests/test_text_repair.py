"""Recuperação de PDFs com tabela de codificação quebrada."""

import pytest

from src.etl.transform.text_repair import (
  detect_repair,
  is_broken,
  shift_text,
)

# Trecho real da prova de química de 2018: a fonte não tem ToUnicode e o
# PyMuPDF devolve os códigos de glifo, deslocados uniformemente em 29.
BROKEN_2018 = (
  "\x03\n&RQVWDQWH\x03GH\x03$YRJDGUR\x03\x0b1$\x0c\x03 \x03 \x19\x0f\x13\x15\x03[\x03"
  "\x14\x13\x15\x16\x03P\n&RQVWDQWH\x03GH\x03)DUDGD\\\x03\x0b)\x0c\x03\x03\n \x03 "
  "\x1c\x0f\x19\x18\x03[\x03\x14\x13\x17\x03&\x03\n9ROXPH\x03PRODU\x03GH\x03J\x86V"
)

# Trecho real de uma prova de inglês da pós: índices de glifo sem relação
# linear com Unicode. Nenhum deslocamento recupera este caso.
BROKEN_NONLINEAR = (
  "\x00\x01\x02\x00\x00\x03\x04\x05\x00\x00\x06\x05\x07\x08\x00\x00\t\x06\n\x0b\x0c"
  "\r\x00\x00\x0e\x07\x0f\x07\x10\x00\x00\x07\x11\x12\n\x07\x08\x08\x00\x00\n\x07"
  "\x13\n\x07\x14\x00\x00\x05\x15\x07\n\x00\x00\r\x05\x14\x00\x00\x04\x0c\x15\x0b"
)


def test_healthy_text_is_left_alone():
  clean = "Questão 1. Seja z um número complexo de módulo unitário."
  assert not is_broken(clean)
  assert detect_repair(clean) is None


def test_uniform_shift_is_recovered():
  assert is_broken(BROKEN_2018)
  repair = detect_repair(BROKEN_2018)
  assert repair is not None
  assert repair.shift == 29
  recovered = repair.apply(BROKEN_2018)
  assert "Constante de Avogadro" in recovered
  assert "Faraday" in recovered


def test_nonlinear_encoding_is_rejected():
  """Precisa devolver None, não um deslocamento qualquer.

  Antes do piso absoluto de pontuação, um deslocamento que produzisse
  `)*+)),-.))/.01))` era aceito: 94% de caracteres imprimíveis, 0% de texto.
  Pior que devolver lixo, isso impedia o OCR de assumir o documento.
  """
  assert is_broken(BROKEN_NONLINEAR)
  assert detect_repair(BROKEN_NONLINEAR) is None


def test_score_rejects_punctuation_soup():
  """O deslocamento que gerava o falso positivo continua sendo rejeitado."""
  soup = shift_text(BROKEN_NONLINEAR, 41)
  assert detect_repair(soup) is None or not soup.strip()


@pytest.mark.parametrize("shift", [1, 29, 64])
def test_shift_is_reversible(shift):
  original = "Constante de Avogadro (NA) = 6,02"
  assert shift_text(shift_text(original, shift), -shift) == original


def test_shift_preserves_newlines():
  # Quebras de linha não podem ser deslocadas: os limites de linha vêm do
  # layout, não do encoding.
  assert shift_text("ab\ncd", 1).count("\n") == 1
