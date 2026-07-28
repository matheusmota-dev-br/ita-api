"""Decisões de revisão humana, persistidas fora do banco.

O `.db` é um artefato descartável: `task db-rebuild` apaga o arquivo inteiro. O
upsert do load protege linhas `human_verified` de serem sobrescritas, mas não
protege contra o rebuild. Sem este arquivo, uma tarde inteira de auditoria se
perderia na próxima reconstrução.

Um registro carrega três coisas independentes:

- `status` — o veredito da conferência.
- `needs_llm` — override manual da triagem. `True` força o enrich mesmo que o
  texto pareça limpo; `False` o dispensa; ausente deixa a heurística decidir.
- `statement_markdown` / `alternatives` — a correção feita à mão. Uma questão
  corrigida manualmente não precisa passar pelo LLM, e é a forma mais barata
  de fechar um caso.
- `bbox` — o retângulo corrigido, quando a detecção pegou demais ou de menos.
  Ao contrário dos outros campos, este muda o que é *extraído*: o recorte é
  regerado e o texto reextraído a partir dele.

A chave é (caminho do PDF, número da questão) — legível e estável entre
reconstruções, ao contrário do `questions.id`, que é autoincremento.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from src.config import PROJECT_ROOT

DECISIONS_PATH = PROJECT_ROOT / "data" / "review" / "decisions.json"

# Trilha append-only. O decisions.json guarda o estado final de cada questão —
# é o que o load aplica. Este arquivo guarda a sequência de ações que levou até
# lá, para que uma auditoria consiga responder "quem mudou o quê, quando" e não
# só "como está agora".
AUDIT_PATH = PROJECT_ROOT / "data" / "review" / "audit.jsonl"


def _key(local_path: str, number: int) -> str:
  return f"{local_path}#{number}"


def load_decisions(path: Path | None = None) -> dict[str, dict]:
  path = path or DECISIONS_PATH
  if not path.exists():
    return {}
  return json.loads(path.read_text(encoding="utf-8"))


def record_decision(
  *,
  local_path: str,
  number: int,
  status: str | None = None,
  note: str | None = None,
  needs_llm: bool | None = None,
  statement_markdown: str | None = None,
  alternatives: dict[str, str] | None = None,
  bbox: dict | None = None,
  path: Path | None = None,
) -> Path:
  """Grava ou atualiza uma decisão, preservando os campos não informados.

  O merge é deliberado: marcar "não precisa de LLM" não pode apagar uma
  correção de texto feita antes, nem vice-versa.
  """
  path = path or DECISIONS_PATH
  decisions = load_decisions(path)
  key = _key(local_path, number)
  entry = decisions.get(key, {"local_path": local_path, "number": number})
  before = dict(entry)

  if status is not None:
    entry["status"] = status
  if note is not None:
    entry["note"] = note
  if needs_llm is not None:
    entry["needs_llm"] = needs_llm
  if statement_markdown is not None:
    entry["statement_markdown"] = statement_markdown
  if alternatives is not None:
    entry["alternatives"] = {**entry.get("alternatives", {}), **alternatives}
  if bbox is not None:
    entry["bbox"] = bbox
  entry["reviewed_at"] = datetime.now(timezone.utc).isoformat()

  decisions[key] = entry
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(
    json.dumps(decisions, indent=2, ensure_ascii=False, sort_keys=True),
    encoding="utf-8",
  )

  _append_audit(
    path.parent / AUDIT_PATH.name,
    {
      "at": entry["reviewed_at"],
      "key": key,
      "changed": {
        field: value
        for field, value in {
          "status": status,
          "note": note,
          "needs_llm": needs_llm,
          "statement_markdown": statement_markdown,
          "alternatives": alternatives,
          "bbox": bbox,
        }.items()
        if value is not None
      },
      "before": {field: before.get(field) for field in changed_fields(before, entry)},
    },
  )
  return path


def changed_fields(before: dict, after: dict) -> list[str]:
  return sorted(
    {
      k
      for k in set(before) | set(after)
      if k != "reviewed_at" and before.get(k) != after.get(k)
    }
  )


def _append_audit(path: Path, record: dict) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  with path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def load_audit(path: Path | None = None) -> list[dict]:
  """Lê a trilha completa, em ordem cronológica."""
  path = path or AUDIT_PATH
  if not path.exists():
    return []
  return [
    json.loads(line)
    for line in path.read_text(encoding="utf-8").splitlines()
    if line.strip()
  ]


def decision_for(
  decisions: dict[str, dict], local_path: str, number: int
) -> dict | None:
  return decisions.get(_key(local_path, number))
