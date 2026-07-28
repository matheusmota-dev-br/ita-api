"""Captura o que afeta a saída do pipeline, para que uma reexecução seja comparável.

Reproduzir não é só rodar os mesmos comandos: a mesma prova processada com
outra versão do PyMuPDF ou do Tesseract pode render um número diferente de
questões. Este módulo registra o ambiente junto do resultado, para que uma
divergência possa ser atribuída a algo concreto em vez de virar mistério.

O que é registrado foi escolhido por afetar a saída de fato:

- `transform_version` — bump manual, invalida manifests.
- PyMuPDF e MuPDF — mudam extração de texto e rasterização.
- Tesseract e idiomas — mudam o OCR dos 11 documentos digitalizados.
- `git_sha` e se a árvore está suja — identifica o código exato.
- `prompt_version` e modelo — identificam o enrich.
"""

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

from src.config import PROJECT_ROOT

PROVENANCE_PATH = PROJECT_ROOT / "data" / "processed" / "provenance.json"


def _run(*args: str) -> str | None:
  try:
    out = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
  except (OSError, subprocess.SubprocessError):
    return None
  return out.stdout.strip() or None


def git_state() -> dict:
  sha = _run("git", "rev-parse", "HEAD")
  status = _run("git", "status", "--porcelain")
  return {
    "sha": sha,
    # Uma árvore suja significa que o sha não descreve o código que rodou.
    "dirty": bool(status),
    "dirty_files": len(status.splitlines()) if status else 0,
  }


def tesseract_state() -> dict:
  version = _run("tesseract", "--version")
  langs = _run("tesseract", "--list-langs")
  return {
    "available": version is not None,
    "version": version.splitlines()[0] if version else None,
    "languages": sorted(langs.splitlines()[1:]) if langs else [],
  }


def pymupdf_state() -> dict:
  try:
    import pymupdf
  except ImportError:
    return {"available": False}
  return {
    "available": True,
    "pymupdf": getattr(pymupdf, "__version__", None),
    "mupdf": getattr(pymupdf, "mupdf_version", None),
  }


def capture() -> dict:
  from src.etl.enrich.prompt import PROMPT_VERSION
  from src.etl.transform.manifest import SCHEMA_VERSION, TRANSFORM_VERSION

  return {
    "schema_version": SCHEMA_VERSION,
    "transform_version": TRANSFORM_VERSION,
    "prompt_version": PROMPT_VERSION,
    "python": sys.version.split()[0],
    "platform": platform.platform(),
    "git": git_state(),
    "pymupdf": pymupdf_state(),
    "tesseract": tesseract_state(),
  }


def write(path: Path | None = None) -> Path:
  path = path or PROVENANCE_PATH
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(
    json.dumps(capture(), indent=2, ensure_ascii=False, sort_keys=True),
    encoding="utf-8",
  )
  return path


def read(path: Path | None = None) -> dict | None:
  path = path or PROVENANCE_PATH
  if not path.exists():
    return None
  return json.loads(path.read_text(encoding="utf-8"))


def fingerprint(values: dict) -> str:
  """Hash estável de um conjunto de contagens, para comparar execuções."""
  payload = json.dumps(values, sort_keys=True, ensure_ascii=False)
  return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
