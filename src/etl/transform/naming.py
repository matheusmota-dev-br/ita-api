"""Traduz o nome de um PDF baixado em uma referência de prova.

O nome sozinho é ambíguo: `prova_ita_vestibular_2012_fisica.pdf` e
`prova_ita_vestibular_2021_fisica.pdf` têm a mesma forma, mas o primeiro é uma
prova de fase única e o segundo é de 2ª fase. Quem desambigua é o sidecar
`_index.json` gravado pelo extract, que guarda de qual das três tabelas do site
cada arquivo veio.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from src.constants import Discipline, FormatType, TestType
from src.etl.extract._download_pdfs_from_jsons import load_index
from src.models.enums import DocumentKind, Phase

FILENAME_REGEX = re.compile(
  r"^prova_ita_(?P<test_type>vestibular|pos)_(?P<year>\d{4})_(?P<column>.+)\.pdf$"
)

# Era mais antiga do site (formato 1): fase única, um PDF por matéria.
FORMAT_INDEX_UNICA = 1

_DISCIPLINE_BY_COLUMN = {
  "matematica": Discipline.MATEMATICA,
  "fisica": Discipline.FISICA,
  "quimica": Discipline.QUIMICA,
  "portugues": Discipline.PORTUGUES,
  "ingles": Discipline.INGLES,
  "redacao": Discipline.REDACAO,
}

_POS_COLUMNS = {
  "1o_semestre_matematica": (Phase.POS_1SEM, Discipline.MATEMATICA),
  "1o_semestre_ingles": (Phase.POS_1SEM, Discipline.INGLES),
  "2o_semestre_matematica": (Phase.POS_2SEM, Discipline.MATEMATICA),
  "2o_semestre_ingles": (Phase.POS_2SEM, Discipline.INGLES),
}

_GABARITO_PHASES = {
  "answer_key_1f": Phase.PRIMEIRA,
  "answer_key_2f": Phase.SEGUNDA,
  "answer_key": Phase.UNICA,
}


@dataclass(frozen=True)
class ExamRef:
  test_type: TestType
  year: int
  phase: Phase
  discipline: Discipline | None
  kind: DocumentKind
  source_column: str
  format_index: int
  path: Path

  @property
  def exam_slug(self) -> str:
    """Identifica a prova lógica — sem a matéria, que pertence ao documento."""
    return f"{self.test_type.value}-{self.year}-{self.phase.value}"

  @property
  def slug(self) -> str:
    """Identifica o documento; é o nome do diretório de saída do transform."""
    parts = [self.exam_slug]
    if self.discipline is not None:
      parts.append(self.discipline.value)
    if self.kind is DocumentKind.GABARITO:
      parts.append("gabarito")
    return "-".join(parts)

  @property
  def default_format_type(self) -> FormatType:
    """Formato esperado das questões, antes de olhar o conteúdo do PDF."""
    if self.discipline is Discipline.REDACAO:
      return FormatType.ESSAY
    if self.phase in (Phase.PRIMEIRA, Phase.UNICA):
      return FormatType.OBJECTIVE
    if self.test_type is TestType.POS:
      return FormatType.OBJECTIVE
    return FormatType.DISSERTATIVE


def _vestibular_ref(
  column: str, format_index: int
) -> tuple[Phase, Discipline | None, DocumentKind]:
  if column in _GABARITO_PHASES:
    phase = _GABARITO_PHASES[column]
    # Na era de fase única o gabarito cobre a prova inteira.
    if format_index == FORMAT_INDEX_UNICA:
      phase = Phase.UNICA
    return phase, None, DocumentKind.GABARITO

  if column == "prova_1f":
    return Phase.PRIMEIRA, None, DocumentKind.PROVA

  discipline = _DISCIPLINE_BY_COLUMN.get(column)
  if discipline is None:
    raise ValueError(f"coluna desconhecida: {column!r}")

  # A mesma coluna significa coisas diferentes conforme a era: em 2008–2018 é
  # uma prova de fase única, de 2019 em diante é uma prova de 2ª fase.
  phase = Phase.UNICA if format_index == FORMAT_INDEX_UNICA else Phase.SEGUNDA
  return phase, discipline, DocumentKind.PROVA


def parse_pdf_path(path: Path, index: dict[str, dict] | None = None) -> ExamRef | None:
  """Devolve a ExamRef de um PDF, ou None se o nome não for reconhecido.

  Retorna None em vez de levantar para que o driver de lote consiga logar e
  seguir em frente diante de um arquivo inesperado no diretório.
  """
  match = FILENAME_REGEX.match(path.name)
  if match is None:
    return None

  test_type = TestType(match.group("test_type"))
  year = int(match.group("year"))
  column = match.group("column")

  if index is None:
    index = load_index(path.parent)
  entry = index.get(path.name, {})
  format_index = entry.get("format_index", 0)

  try:
    if test_type is TestType.POS:
      if column not in _POS_COLUMNS:
        return None
      phase, discipline = _POS_COLUMNS[column]
      kind = DocumentKind.PROVA
    else:
      phase, discipline, kind = _vestibular_ref(column, format_index)
  except ValueError:
    return None

  return ExamRef(
    test_type=test_type,
    year=year,
    phase=phase,
    discipline=discipline,
    kind=kind,
    source_column=column,
    format_index=format_index,
    path=path,
  )


def iter_exam_refs(provas_dir: Path) -> list[ExamRef]:
  """Todas as ExamRefs de um diretório de provas, em ordem estável."""
  index = load_index(provas_dir)
  refs = [parse_pdf_path(p, index) for p in sorted(provas_dir.glob("*.pdf"))]
  return [r for r in refs if r is not None]
