"""Rasteriza uma região do PDF em PNG e extrai o texto dela.

Quatro caminhos precisam produzir exatamente o mesmo artefato a partir do mesmo
retângulo: o transform ao detectar uma questão, a interface ao corrigir um
recorte à mão, a interface ao criar uma questão nova, e o load ao reconstruir
uma questão criada manualmente depois de um `--rebuild`.

Se cada um rasterizasse do seu jeito — outro DPI, outra ordem de extração — um
recorte corrigido ficaria distinguível de um detectado, e o fingerprint de
reprodutibilidade deixaria de fechar sem que nada de fato tivesse mudado. Daí
este módulo ser a única definição do que é "recortar".
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from src.etl.transform.constants import MIN_CLIP_SIZE

# Mesma resolução usada pelo transform ao exportar os recortes do pipeline.
CROP_DPI = 150


@dataclass(frozen=True)
class Crop:
  """O recorte materializado: o que vai para o disco e para o banco."""

  png: bytes
  text: str
  width_px: int
  height_px: int
  sha256: str

  @property
  def byte_size(self) -> int:
    return len(self.png)


class CropError(ValueError):
  """Retângulo que não dá para rasterizar."""


def render_crop(
  pdf_path: Path,
  page_no: int,
  rect: tuple[float, float, float, float],
  dpi: int = CROP_DPI,
) -> Crop:
  """Recorta `rect` da página `page_no`, devolvendo PNG e texto.

  O texto é reextraído do próprio retângulo em vez de ser herdado: o recorte é
  a definição do que é a questão, então mudar o retângulo tem que mudar o texto
  junto, senão os dois ficam contando histórias diferentes.
  """
  import pymupdf

  x0, y0, x1, y1 = rect
  if x1 - x0 < MIN_CLIP_SIZE or y1 - y0 < MIN_CLIP_SIZE:
    raise CropError(f"retangulo sem area: {rect}")

  if not pdf_path.is_file():
    raise CropError(f"PDF ausente: {pdf_path}")

  document = pymupdf.open(pdf_path)
  try:
    if not 0 <= page_no < len(document):
      raise CropError(f"pagina {page_no} fora do documento ({len(document)} paginas)")
    page = document[page_no]
    clip = pymupdf.Rect(x0, y0, x1, y1) & page.rect
    if clip.is_empty or clip.width < MIN_CLIP_SIZE or clip.height < MIN_CLIP_SIZE:
      raise CropError(f"retangulo fora da pagina: {rect}")
    pixmap = page.get_pixmap(clip=clip, dpi=dpi, alpha=False)
    png = pixmap.tobytes("png")
    text = page.get_text(clip=clip, sort=True).strip()
    width, height = pixmap.width, pixmap.height
  finally:
    document.close()

  return Crop(
    png=png,
    text=text,
    width_px=width,
    height_px=height,
    sha256=hashlib.sha256(png).hexdigest(),
  )


def write_crop(crop: Crop, target: Path) -> Path:
  target.parent.mkdir(parents=True, exist_ok=True)
  target.write_bytes(crop.png)
  return target
