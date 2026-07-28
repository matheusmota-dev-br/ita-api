"""Leitura dos artefatos que o transform gravou em data/processed/."""

import json
from dataclasses import dataclass
from pathlib import Path

from src.etl.transform.paths import (
  ANSWER_KEY_FILENAME,
  MANIFEST_FILENAME,
  get_processed_root,
)


@dataclass
class LoadedDocument:
  directory: Path
  manifest: dict | None
  answer_key: dict | None

  @property
  def payload(self) -> dict:
    """O JSON que descreve este documento, seja prova ou gabarito."""
    return self.manifest or self.answer_key or {}

  @property
  def slug(self) -> str:
    return self.payload.get("slug", self.directory.name)


def iter_processed(root: Path | None = None) -> list[LoadedDocument]:
  """Todos os documentos processados, em ordem estável por slug."""
  root = root or get_processed_root()
  if not root.is_dir():
    return []

  out: list[LoadedDocument] = []
  for directory in sorted(p for p in root.iterdir() if p.is_dir()):
    manifest_path = directory / MANIFEST_FILENAME
    answer_key_path = directory / ANSWER_KEY_FILENAME
    manifest = (
      json.loads(manifest_path.read_text(encoding="utf-8"))
      if manifest_path.exists()
      else None
    )
    answer_key = (
      json.loads(answer_key_path.read_text(encoding="utf-8"))
      if answer_key_path.exists()
      else None
    )
    if manifest or answer_key:
      out.append(LoadedDocument(directory, manifest, answer_key))
  return out
