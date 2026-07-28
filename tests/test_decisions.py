"""Persistência da revisão humana e sua trilha de auditoria."""

import json

from src.etl.enrich.triage import triage
from src.etl.load.decisions import load_audit, load_decisions, record_decision


def test_fields_merge_instead_of_replacing(tmp_path):
  """Marcar "dispensa LLM" não pode apagar uma correção de texto anterior.

  Cada campo é uma decisão independente, tomada em momentos diferentes da
  auditoria; um PUT que sobrescrevesse o registro perderia trabalho.
  """
  path = tmp_path / "d.json"
  record_decision(local_path="p.pdf", number=1, statement_markdown="$x^2$", path=path)
  record_decision(local_path="p.pdf", number=1, needs_llm=False, path=path)
  record_decision(local_path="p.pdf", number=1, status="human_verified", path=path)

  entry = load_decisions(path)["p.pdf#1"]
  assert entry["statement_markdown"] == "$x^2$"
  assert entry["needs_llm"] is False
  assert entry["status"] == "human_verified"


def test_alternatives_merge_per_letter(tmp_path):
  path = tmp_path / "d.json"
  record_decision(local_path="p.pdf", number=1, alternatives={"A": "um"}, path=path)
  record_decision(local_path="p.pdf", number=1, alternatives={"B": "dois"}, path=path)
  assert load_decisions(path)["p.pdf#1"]["alternatives"] == {"A": "um", "B": "dois"}


def test_bbox_is_recorded(tmp_path):
  path = tmp_path / "d.json"
  bbox = {"page_no": 2, "part_index": 0, "x0": 10, "y0": 20, "x1": 500, "y1": 300}
  record_decision(local_path="p.pdf", number=7, bbox=bbox, path=path)
  assert load_decisions(path)["p.pdf#7"]["bbox"] == bbox


def test_key_survives_rebuild(tmp_path):
  """A chave é (caminho, número), não o id — que muda a cada --rebuild."""
  path = tmp_path / "d.json"
  record_decision(
    local_path="data/x/prova.pdf", number=13, status="rejected", path=path
  )
  assert "data/x/prova.pdf#13" in load_decisions(path)


def test_audit_trail_is_append_only(tmp_path):
  """O decisions.json guarda o estado final; a trilha guarda como se chegou lá."""
  path = tmp_path / "d.json"
  record_decision(local_path="p.pdf", number=1, status="human_verified", path=path)
  record_decision(local_path="p.pdf", number=1, status="rejected", path=path)
  record_decision(local_path="p.pdf", number=2, needs_llm=True, path=path)

  audit = load_audit(tmp_path / "audit.jsonl")
  assert len(audit) == 3, "cada acao precisa virar uma linha"
  assert [a["key"] for a in audit] == ["p.pdf#1", "p.pdf#1", "p.pdf#2"]
  assert audit[0]["changed"]["status"] == "human_verified"
  assert audit[1]["changed"]["status"] == "rejected"
  # O estado final tem só o ultimo valor; a trilha preserva os dois.
  assert load_decisions(path)["p.pdf#1"]["status"] == "rejected"


def test_audit_lines_are_valid_json(tmp_path):
  path = tmp_path / "d.json"
  record_decision(
    local_path="p.pdf", number=1, note="acentuação é preservada", path=path
  )
  raw = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip()
  assert json.loads(raw)["changed"]["note"] == "acentuação é preservada"


# --- triagem ---------------------------------------------------------------


def test_clean_text_skips_the_llm():
  clean = (
    "Questão 15. Quando aquecido ao ar, 1,65 g de um determinado elemento X "
    "forma 2,29 g de um óxido de fórmula X3O4."
  )
  assert not triage(clean).needs_llm


def test_corrupted_math_needs_the_llm():
  dirty = "1. Se x̸ = 0 e x = p6xy −9y2, ent˜ao, em termos de y, x ="
  assert triage(dirty).needs_llm


def test_ocr_always_needs_the_llm():
  """O Tesseract lê `x³` como `x)` — o texto parece limpo mas está errado."""
  clean_looking = "Questão 8. Seja p(x) = x) + bx? + cx + d um polinômio."
  assert triage(clean_looking, from_ocr=True).needs_llm


def test_empty_text_needs_the_llm():
  assert triage("").needs_llm
  assert triage("   ").reason == "sem texto"
