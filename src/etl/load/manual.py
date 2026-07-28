"""Questões criadas à mão: a construção do objeto, sem a sessão.

Dois caminhos criam a mesma coisa. A interface cria quando o revisor marca uma
região que a detecção não pegou; o load recria depois de um `--rebuild`, que
apaga o banco inteiro e precisa reconstruir a questão a partir do retângulo
registrado em `decisions.json`.

Os dois usam sessões diferentes — uma async, outra síncrona — mas o objeto
construído é idêntico, então o que se compartilha é a montagem do grafo, não a
persistência. O `cascade="all, delete-orphan"` das relações faz páginas,
alternativas e assets acompanharem a questão num único `session.add`.

O PNG é sempre regerado a partir do bbox em vez de ser lido do disco: o
retângulo é a fonte da verdade registrada, e regerar garante que o artefato
corresponda à decisão mesmo que alguém tenha mexido no arquivo.
"""

from pathlib import Path

from src.config import settings
from src.constants import FormatType
from src.etl.transform.crop import CROP_DPI, Crop, render_crop, write_crop
from src.models import Alternative, Asset, Document, Question
from src.models.enums import AssetKind, DisciplineSource, ReviewStatus
from src.models.question import QuestionPage

# Recortes manuais ficam separados dos do pipeline: o transform escreve em
# `questoes/`, e um `task transform` posterior sobrescreveria — ou deixaria um
# órfão — se os dois dividissem o mesmo diretório.
MANUAL_SUBDIR = "manual"

OPTION_LETTERS = ("A", "B", "C", "D", "E")


def manual_image_name(number: int) -> str:
  return f"{MANUAL_SUBDIR}/questao_{number:02d}.png"


def figure_image_name(number: int, index: int) -> str:
  return f"{MANUAL_SUBDIR}/questao_{number:02d}_figura_{index:02d}.png"


def figure_relative_path(document: Document, number: int, index: int) -> str:
  prefix = document.processed_dir or f"processed/{document.exam_id}"
  return f"{prefix}/{figure_image_name(number, index)}"


def build_figure_asset(bbox: dict, crop: Crop, relative_path: str) -> Asset:
  """Figura anexada a uma questão, impressa longe dela.

  `AssetKind.FIGURE` e **não** um `QuestionPage`: a figura não faz parte do
  retângulo do enunciado, então não pode alterar `page_start`/`page_end` nem
  entrar na reextração de texto. É por isso que anexar uma figura da página 9 a
  uma questão da página 3 não corrompe nada.
  """
  return Asset(
    kind=AssetKind.FIGURE,
    relative_path=relative_path,
    page_no=bbox["page_no"],
    bbox=[bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"]],
    dpi=CROP_DPI,
    width_px=crop.width_px,
    height_px=crop.height_px,
    sha256=crop.sha256,
    byte_size=crop.byte_size,
  )


def render_figure_crop(
  pdf_path: Path, document: Document, number: int, index: int, bbox: dict
) -> tuple[Crop, str]:
  crop = render_crop(
    pdf_path,
    bbox["page_no"],
    (bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"]),
  )
  relative = figure_relative_path(document, number, index)
  write_crop(crop, settings.media_root / relative)
  return crop, relative


def manual_relative_path(document: Document, number: int) -> str:
  prefix = document.processed_dir or f"processed/{document.exam_id}"
  return f"{prefix}/{manual_image_name(number)}"


def render_manual_crop(
  pdf_path: Path, document: Document, number: int, bbox: dict
) -> tuple[Crop, str]:
  """Rasteriza o recorte e o grava no lugar dele. Devolve o caminho relativo."""
  crop = render_crop(
    pdf_path,
    bbox["page_no"],
    (bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"]),
  )
  relative = manual_relative_path(document, number)
  write_crop(crop, settings.media_root / relative)
  return crop, relative


def build_manual_question(
  document: Document, number: int, bbox: dict, crop: Crop, relative_path: str
) -> Question:
  """Monta a questão inteira, pronta para um único `session.add`.

  Nasce `human_verified`: veio de um humano apontando o retângulo, então não
  faz sentido pedir que outro humano a aprove depois.
  """
  question = Question(
    document_id=document.id,
    exam_id=document.exam_id,
    number=number,
    format_type=FormatType.OBJECTIVE,
    discipline=document.discipline,
    discipline_source=DisciplineSource.HUMAN,
    statement_raw=crop.text,
    page_start=bbox["page_no"],
    page_end=bbox["page_no"],
    review_status=ReviewStatus.HUMAN_VERIFIED,
    source_sha256=crop.sha256,
  )
  question.pages.append(
    QuestionPage(
      part_index=0,
      page_no=bbox["page_no"],
      x0=bbox["x0"],
      y0=bbox["y0"],
      x1=bbox["x1"],
      y1=bbox["y1"],
      image_path=manual_image_name(number),
      text_raw=crop.text,
    )
  )
  question.assets.append(
    Asset(
      kind=AssetKind.QUESTION_CROP,
      relative_path=relative_path,
      page_no=bbox["page_no"],
      bbox=[bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"]],
      dpi=CROP_DPI,
      width_px=crop.width_px,
      height_px=crop.height_px,
      sha256=crop.sha256,
      byte_size=crop.byte_size,
    )
  )
  for index, letter in enumerate(OPTION_LETTERS):
    question.alternatives.append(
      Alternative(letter=letter, order_index=index, review_status=ReviewStatus.RAW)
    )
  return question
