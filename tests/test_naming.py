"""A desambiguação das três eras do site é o ponto mais frágil do pipeline.

A mesma coluna `fisica` significa prova de fase única em 2008–2018 e prova de
2ª fase de 2019 em diante; só o `format_index` do sidecar separa os dois casos.
"""

from pathlib import Path

import pytest

from src.constants import Discipline, FormatType, TestType
from src.etl.transform.naming import parse_pdf_path
from src.models.enums import DocumentKind, Phase


def ref(name: str, format_index: int):
  return parse_pdf_path(Path(name), index={name: {"format_index": format_index}})


@pytest.mark.parametrize(
  ("name", "format_index", "phase", "discipline", "kind"),
  [
    # Era 2008–2018: um PDF por matéria, fase única.
    (
      "prova_ita_vestibular_2012_fisica.pdf",
      1,
      Phase.UNICA,
      Discipline.FISICA,
      DocumentKind.PROVA,
    ),
    (
      "prova_ita_vestibular_2012_ingles.pdf",
      1,
      Phase.UNICA,
      Discipline.INGLES,
      DocumentKind.PROVA,
    ),
    (
      "prova_ita_vestibular_2012_answer_key.pdf",
      1,
      Phase.UNICA,
      None,
      DocumentKind.GABARITO,
    ),
    # Era 2019–2024: mesma coluna, significado diferente.
    (
      "prova_ita_vestibular_2021_fisica.pdf",
      2,
      Phase.SEGUNDA,
      Discipline.FISICA,
      DocumentKind.PROVA,
    ),
    (
      "prova_ita_vestibular_2021_prova_1f.pdf",
      2,
      Phase.PRIMEIRA,
      None,
      DocumentKind.PROVA,
    ),
    (
      "prova_ita_vestibular_2021_redacao.pdf",
      2,
      Phase.SEGUNDA,
      Discipline.REDACAO,
      DocumentKind.PROVA,
    ),
    # Era 2025–2026.
    (
      "prova_ita_vestibular_2025_answer_key_1f.pdf",
      3,
      Phase.PRIMEIRA,
      None,
      DocumentKind.GABARITO,
    ),
    (
      "prova_ita_vestibular_2025_answer_key_2f.pdf",
      3,
      Phase.SEGUNDA,
      None,
      DocumentKind.GABARITO,
    ),
    # Pós-graduação.
    (
      "prova_ita_pos_2024_1o_semestre_ingles.pdf",
      0,
      Phase.POS_1SEM,
      Discipline.INGLES,
      DocumentKind.PROVA,
    ),
    (
      "prova_ita_pos_2024_2o_semestre_matematica.pdf",
      0,
      Phase.POS_2SEM,
      Discipline.MATEMATICA,
      DocumentKind.PROVA,
    ),
  ],
)
def test_parses_each_era(name, format_index, phase, discipline, kind):
  parsed = ref(name, format_index)
  assert parsed is not None
  assert parsed.phase is phase
  assert parsed.discipline is discipline
  assert parsed.kind is kind


def test_same_column_different_era_gives_different_phase():
  antiga = ref("prova_ita_vestibular_2012_fisica.pdf", 1)
  moderna = ref("prova_ita_vestibular_2021_fisica.pdf", 2)
  assert antiga.phase is Phase.UNICA
  assert moderna.phase is Phase.SEGUNDA
  assert antiga.slug != moderna.slug


def test_slug_is_unique_per_document():
  slugs = {
    ref("prova_ita_vestibular_2025_prova_1f.pdf", 3).slug,
    ref("prova_ita_vestibular_2025_answer_key_1f.pdf", 3).slug,
    ref("prova_ita_vestibular_2025_matematica.pdf", 3).slug,
    ref("prova_ita_vestibular_2025_answer_key_2f.pdf", 3).slug,
  }
  assert len(slugs) == 4


def test_format_type_by_context():
  assert (
    ref("prova_ita_vestibular_2025_prova_1f.pdf", 3).default_format_type
    is FormatType.OBJECTIVE
  )
  assert (
    ref("prova_ita_vestibular_2025_matematica.pdf", 3).default_format_type
    is FormatType.DISSERTATIVE
  )
  assert (
    ref("prova_ita_vestibular_2021_redacao.pdf", 2).default_format_type
    is FormatType.ESSAY
  )
  assert (
    ref("prova_ita_vestibular_2012_fisica.pdf", 1).default_format_type
    is FormatType.OBJECTIVE
  )


def test_unknown_names_return_none_instead_of_raising():
  # O driver de lote precisa poder logar e seguir em frente.
  assert parse_pdf_path(Path("leiame.txt"), index={}) is None
  assert (
    parse_pdf_path(Path("prova_ita_vestibular_2020_coluna_nova.pdf"), index={}) is None
  )
  assert parse_pdf_path(Path("prova_ita_pos_2020_3o_semestre.pdf"), index={}) is None


def test_test_type_round_trips():
  assert ref("prova_ita_pos_2024_1o_semestre_ingles.pdf", 0).test_type is TestType.POS
  assert (
    ref("prova_ita_vestibular_2025_prova_1f.pdf", 3).test_type is TestType.VESTIBULAR
  )
