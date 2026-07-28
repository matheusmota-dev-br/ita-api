"""Cache em disco das respostas do LLM.

Fica em disco, e não no banco, de propósito: o `.db` é um artefato descartável
que o `--rebuild` apaga. Se o cache morasse nele, cada reconstrução custaria o
gasto inteiro de API de novo.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from src.config import PROJECT_ROOT

CACHE_ROOT = PROJECT_ROOT / "data" / "cache" / "enrich"


def cache_key(source_sha256: str, prompt_version: str, model: str) -> str:
  """Identidade de uma chamada.

  Inclui a versão do prompt e o modelo: bumpar qualquer um dos dois invalida o
  cache sem precisar apagar nada. O `source_sha256` vem do manifest e só muda se
  o texto ou o recorte da questão mudarem.
  """
  raw = f"{source_sha256}|{prompt_version}|{model}"
  return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class CacheEntry:
  key: str
  payload: dict
  usage: dict


class EnrichCache:
  def __init__(self, root: Path | None = None) -> None:
    self.root = root or CACHE_ROOT
    self.root.mkdir(parents=True, exist_ok=True)

  def _path(self, key: str, suffix: str) -> Path:
    # Dois níveis de subdiretório: milhares de arquivos num diretório só ficam
    # lentos de listar em alguns sistemas de arquivos.
    return self.root / key[:2] / f"{key}{suffix}"

  def get(self, key: str) -> CacheEntry | None:
    path = self._path(key, ".json")
    if not path.exists():
      return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return CacheEntry(key=key, payload=data["payload"], usage=data.get("usage", {}))

  def put(self, key: str, payload: dict, usage: dict) -> None:
    path = self._path(key, ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
      json.dumps({"payload": payload, "usage": usage}, indent=2, ensure_ascii=False),
      encoding="utf-8",
    )
    self._path(key, ".error.json").unlink(missing_ok=True)

  def put_error(self, key: str, status: str, message: str) -> None:
    path = self._path(key, ".error.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
      json.dumps({"status": status, "message": message}, ensure_ascii=False, indent=2),
      encoding="utf-8",
    )

  def has_error(self, key: str) -> bool:
    return self._path(key, ".error.json").exists()

  def stats(self) -> dict[str, int]:
    return {
      "cached": sum(
        1 for _ in self.root.rglob("*.json") if not _.name.endswith(".error.json")
      ),
      "errors": sum(1 for _ in self.root.rglob("*.error.json")),
    }
