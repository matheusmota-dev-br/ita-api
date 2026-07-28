"""Decisões de revisão humana, persistidas fora do banco.

O `.db` é um artefato descartável: `task db-rebuild` apaga o arquivo inteiro. O
upsert do load protege linhas `human_verified` de serem sobrescritas, mas não
protege contra o rebuild. Sem este arquivo, uma tarde inteira de auditoria se
perderia na próxima reconstrução.

O arquivo tem duas seções, porque são duas categorias de decisão com regras de
replay diferentes:

**`questions`** — decisões *sobre* uma questão que existe. Um registro carrega
campos independentes, mesclados e não substituídos:

- `status` — o veredito da conferência.
- `needs_llm` — override manual da triagem. `True` força o enrich mesmo que o
  texto pareça limpo; `False` o dispensa; ausente deixa a heurística decidir.
- `statement_markdown` / `alternatives` — a correção feita à mão. Uma questão
  corrigida manualmente não precisa passar pelo LLM, e é a forma mais barata
  de fechar um caso.
- `bbox` — o retângulo corrigido, quando a detecção pegou demais ou de menos.
  Ao contrário dos outros campos, este muda o que é *extraído*: o recorte é
  regerado e o texto reextraído a partir dele.

**`operations`** — decisões que mudam *quais* questões existem: criar, excluir,
renumerar. Precisam ser uma lista ordenada e não um mapa, porque o resultado
depende da ordem: renumerar 5→6 e depois 6→7 não é o mesmo que o contrário.

A chave de `questions` é (caminho do PDF, número) — legível e estável entre
reconstruções, ao contrário do `questions.id`, que é autoincremento.

Isso cria uma armadilha específica em renumerar: mudar o número **move a chave**.
Uma questão corrigida à mão como nº 5 que vira nº 6 perderia a correção, porque
o load procuraria a decisão em `path#5` e encontraria a questão em `path#6`. Por
isso `record_operation` migra a chave junto com a renumeração, e é também por
isso que a operação fica registrada: sem o histórico, não haveria como explicar
por que uma decisão está sob um número que a detecção nunca produziu.
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

FORMAT_VERSION = 2

OP_CREATE = "create"
OP_DELETE = "delete"
OP_RENUMBER = "renumber"
OPERATIONS = (OP_CREATE, OP_DELETE, OP_RENUMBER)


def _key(local_path: str, number: int) -> str:
  return f"{local_path}#{number}"


def _now() -> str:
  return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> dict:
  """Lê o arquivo bruto, normalizando o formato v1 (mapa plano) para o v2."""
  if not path.exists():
    return {"version": FORMAT_VERSION, "questions": {}, "operations": []}
  raw = json.loads(path.read_text(encoding="utf-8"))
  if "questions" not in raw:
    # v1: o arquivo inteiro era o mapa de decisões por questão.
    return {"version": FORMAT_VERSION, "questions": raw, "operations": []}
  raw.setdefault("operations", [])
  raw.setdefault("questions", {})
  return raw


def _write(path: Path, data: dict) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  data["version"] = FORMAT_VERSION
  path.write_text(
    json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True),
    encoding="utf-8",
  )


def load_decisions(path: Path | None = None) -> dict[str, dict]:
  """Decisões por questão. Aceita tanto o formato v1 quanto o v2."""
  return _read(path or DECISIONS_PATH)["questions"]


def load_operations(path: Path | None = None) -> list[dict]:
  """Operações estruturais, em ordem cronológica de registro."""
  return _read(path or DECISIONS_PATH)["operations"]


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
  data = _read(path)
  decisions = data["questions"]
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
  entry["reviewed_at"] = _now()

  decisions[key] = entry
  _write(path, data)

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


def record_operation(
  *,
  op: str,
  local_path: str,
  number: int,
  bbox: dict | None = None,
  to: int | None = None,
  swap: bool = False,
  note: str | None = None,
  path: Path | None = None,
) -> Path:
  """Registra uma mudança em *quais* questões existem.

  Além de anexar à lista de operações, mantém a seção `questions` coerente:
  renumerar move a decisão junto (e troca as duas, num swap), excluir descarta
  a decisão órfã. Sem isso, uma correção de texto ficaria colada a um número
  que já não é o daquela questão.
  """
  if op not in OPERATIONS:
    raise ValueError(f"operacao desconhecida: {op!r}")
  if op == OP_CREATE and bbox is None:
    raise ValueError("criar uma questao exige o retangulo de origem")
  if op == OP_RENUMBER and to is None:
    raise ValueError("renumerar exige o numero de destino")

  path = path or DECISIONS_PATH
  data = _read(path)
  decisions = data["questions"]

  record = {
    "at": _now(),
    "op": op,
    "local_path": local_path,
    "number": number,
  }
  if bbox is not None:
    record["bbox"] = bbox
  if to is not None:
    record["to"] = to
  if swap:
    record["swap"] = True
  if note:
    record["note"] = note

  if op == OP_DELETE:
    decisions.pop(_key(local_path, number), None)
  elif op == OP_RENUMBER:
    _move_decision(decisions, local_path, number, to, swap=swap)

  data["operations"].append(record)
  _write(path, data)
  _append_audit(
    path.parent / AUDIT_PATH.name, {**record, "key": _key(local_path, number)}
  )
  return path


def _move_decision(
  decisions: dict[str, dict],
  local_path: str,
  number: int,
  to: int,
  *,
  swap: bool,
) -> None:
  source, target = _key(local_path, number), _key(local_path, to)
  moving = decisions.pop(source, None)
  displaced = decisions.pop(target, None) if swap else None

  if moving is not None:
    decisions[target] = {**moving, "number": to}
  if displaced is not None:
    decisions[source] = {**displaced, "number": number}


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
