"""O manifest é o contrato entre o transform e os estágios seguintes.

Sem ele, o enrich e o load teriam que redescobrir nomes de arquivo por
convenção — exatamente o acoplamento que faz um pipeline quebrar em silêncio
quando a convenção muda. Tudo que o load precisa saber sobre uma prova está
aqui; os PNGs e TXTs são referenciados por caminho relativo ao diretório do
manifest.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1
# Bump manual: invalida manifests antigos e força reprocessamento.
TRANSFORM_VERSION = "0.1.0"

# Separador de campos no hash de conteúdo. NUL não pode aparecer no texto
# extraído, então dois conjuntos de campos diferentes nunca colidem.
SEPARATOR = "\x00"


@dataclass
class PartManifest:
  part_index: int
  page_no: int
  bbox: list[float]
  image: str | None = None
  image_sha256: str | None = None


@dataclass
class QuestionManifest:
  number: int
  format_type: str
  page_start: int
  page_end: int
  parts: list[PartManifest]
  text_inside: str | None = None
  text_outside: str | None = None
  discipline: str | None = None
  discipline_source: str | None = None
  discipline_confidence: float | None = None
  source_sha256: str = ""


@dataclass
class RegionManifest:
  page_no: int
  label: str
  image: str
  sha256: str


@dataclass
class DocumentManifest:
  local_path: str
  sha256: str
  page_count: int
  source_url: str | None = None


@dataclass
class ExamManifest:
  schema_version: int
  transform_version: str
  slug: str
  exam_slug: str
  test_type: str
  year: int
  phase: str
  discipline: str | None
  kind: str
  source_column: str
  format_index: int
  label_style: str | None
  document: DocumentManifest
  questions: list[QuestionManifest] = field(default_factory=list)
  regions: list[RegionManifest] = field(default_factory=list)
  warnings: list[str] = field(default_factory=list)

  def to_json(self) -> str:
    return json.dumps(asdict(self), indent=2, ensure_ascii=False)

  def write(self, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(self.to_json(), encoding="utf-8")


def read_manifest(path: Path) -> dict | None:
  if not path.exists():
    return None
  return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1 << 20), b""):
      digest.update(chunk)
  return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
  return hashlib.sha256(data).hexdigest()


def question_source_sha256(statement_raw: str, image_hashes: list[str]) -> str:
  """Identidade do conteúdo de uma questão.

  É a chave de cache do LLM e o que permite ao load pular escritas que não
  mudariam nada: se texto e recortes saem idênticos, nada a jusante reexecuta.
  """
  parts = [statement_raw, *image_hashes]
  return hashlib.sha256(SEPARATOR.join(parts).encode("utf-8")).hexdigest()
