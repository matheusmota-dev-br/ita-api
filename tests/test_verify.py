"""Fingerprint do resultado e captura do ambiente.

Documentar os passos não prova reprodutibilidade. Este módulo é o que prova, e
portanto ele mesmo precisa de prova: um `verify` que aprovasse um corpus errado
seria pior que não ter verificação nenhuma, porque daria confiança falsa.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.constants import Discipline, FormatType, TestType
from src.etl import provenance
from src.etl.verify import _diff, cmd_snapshot, cmd_verify, main, measure
from src.models import Document, Exam, Question
from src.models.enums import DocumentKind, Phase, ReviewStatus
from tests.conftest import create_schema


@pytest.fixture
def corpus_db(tmp_path, monkeypatch):
  """Banco mínimo com duas questões, uma delas revisada."""
  db = tmp_path / "verify.db"
  engine = create_engine(f"sqlite:///{db}")
  create_schema(engine)
  with Session(engine) as session:
    exam = Exam(test_type=TestType.VESTIBULAR, year=2024, phase=Phase.PRIMEIRA)
    session.add(exam)
    session.flush()
    document = Document(
      exam_id=exam.id,
      kind=DocumentKind.PROVA,
      source_column="prova_1f",
      local_path="x.pdf",
      sha256="s",
    )
    session.add(document)
    session.flush()
    for number, status in ((1, ReviewStatus.HUMAN_VERIFIED), (2, ReviewStatus.RAW)):
      session.add(
        Question(
          document_id=document.id,
          exam_id=exam.id,
          number=number,
          format_type=FormatType.OBJECTIVE,
          discipline=Discipline.MATEMATICA,
          statement_raw=f"enunciado {number}",
          page_start=0,
          page_end=0,
          source_sha256=f"h{number}",
          review_status=status,
        )
      )
    session.commit()
  engine.dispose()
  monkeypatch.setattr("src.config.settings.database_url", f"sqlite+aiosqlite:///{db}")
  return db


def test_measure_counts_what_matters(corpus_db):
  state = measure()
  assert state["counts"]["questions"] == 2
  assert state["counts"]["documents"] == 1
  assert state["counts"]["human_verified"] == 1
  assert state["counts"]["questions_with_text"] == 2
  assert state["by_discipline"] == {"matematica": 2}
  assert state["by_year"] == {"2024": 2}


def test_measure_ignores_autoincrement_ids(corpus_db):
  """Nenhum id pode entrar no fingerprint: eles mudam a cada --rebuild."""
  flat = json.dumps(measure())
  assert '"id"' not in flat


def test_snapshot_then_verify_agrees(corpus_db, tmp_path, capsys):
  baseline = tmp_path / "baseline.json"
  assert cmd_snapshot(baseline) == 0
  assert cmd_verify(baseline) == 0
  assert "OK" in capsys.readouterr().out


def test_verify_detects_a_missing_question(corpus_db, tmp_path, capsys):
  """O caso que importa: perder dado tem que virar divergência, não silêncio."""
  baseline = tmp_path / "baseline.json"
  cmd_snapshot(baseline)

  engine = create_engine(f"sqlite:///{corpus_db}")
  with Session(engine) as session:
    session.delete(session.get(Question, 2))
    session.commit()
  engine.dispose()

  assert cmd_verify(baseline) == 1
  out = capsys.readouterr().out
  assert "DIVERGENCIA" in out
  assert "counts.questions: esperado 2, obtido 1 (-1)" in out


def test_verify_without_baseline_fails_loudly(tmp_path, capsys):
  assert cmd_verify(tmp_path / "ausente.json") == 1
  assert "nenhum baseline" in capsys.readouterr().err


def test_baseline_records_the_environment(corpus_db, tmp_path):
  baseline = tmp_path / "baseline.json"
  cmd_snapshot(baseline)
  payload = json.loads(baseline.read_text(encoding="utf-8"))
  assert payload["environment"]["python"]
  assert "pymupdf" in payload["environment"]
  assert len(payload["fingerprint"]) == 16


def test_main_dispatches_snapshot_and_verify(corpus_db, tmp_path, monkeypatch):
  baseline = tmp_path / "b.json"
  monkeypatch.setattr("sys.argv", ["verify", "--snapshot", "--baseline", str(baseline)])
  assert main() == 0
  monkeypatch.setattr("sys.argv", ["verify", "--baseline", str(baseline)])
  assert main() == 0


# --- diff ------------------------------------------------------------------


def test_diff_reports_the_delta_for_numbers():
  assert _diff({"a": 10}, {"a": 7}) == ["a: esperado 10, obtido 7 (-3)"]


def test_diff_recurses_into_nested_maps():
  out = _diff({"c": {"x": 1}}, {"c": {"x": 2}})
  assert out == ["c.x: esperado 1, obtido 2 (+1)"]


def test_diff_reports_keys_that_appeared_or_vanished():
  assert _diff({}, {"novo": 3}) == ["novo: esperado None, obtido 3"]
  assert _diff({"foi": 3}, {}) == ["foi: esperado 3, obtido None"]


def test_diff_is_empty_when_equal():
  assert _diff({"a": 1, "b": {"c": 2}}, {"a": 1, "b": {"c": 2}}) == []


# --- proveniência ----------------------------------------------------------


def test_capture_records_what_changes_the_output():
  env = provenance.capture()
  assert env["python"]
  assert env["transform_version"]
  assert env["prompt_version"]
  assert set(env["pymupdf"]) >= {"available"}
  assert set(env["tesseract"]) >= {"available", "version", "languages"}
  assert set(env["git"]) == {"sha", "dirty", "dirty_files"}


def test_write_and_read_roundtrip(tmp_path):
  path = provenance.write(tmp_path / "prov.json")
  written = provenance.read(path)
  # Compara chave a chave em vez do dicionário inteiro: `git.dirty_files` muda
  # enquanto o próprio teste roda, e afirmar igualdade total seria instável.
  live = provenance.capture()
  assert set(written) == set(live)
  assert written["python"] == live["python"]
  assert written["transform_version"] == live["transform_version"]
  assert written["tesseract"] == live["tesseract"]


def test_read_missing_file_is_none(tmp_path):
  assert provenance.read(tmp_path / "nao-existe.json") is None


def test_fingerprint_is_stable_and_order_independent():
  a = provenance.fingerprint({"x": 1, "y": 2})
  b = provenance.fingerprint({"y": 2, "x": 1})
  assert a == b
  assert a != provenance.fingerprint({"x": 1, "y": 3})
  assert len(a) == 16


def test_run_returns_none_for_a_missing_binary():
  """Sem Tesseract o pipeline roda; a captura não pode explodir."""
  assert provenance._run("binario-que-nao-existe-xyz") is None


def test_tesseract_state_shape_without_the_binary(monkeypatch):
  monkeypatch.setattr(provenance, "_run", lambda *a: None)
  state = provenance.tesseract_state()
  assert state == {"available": False, "version": None, "languages": []}


def test_pymupdf_state_when_absent(monkeypatch):
  real_import = (
    __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__
  )

  def fake_import(name, *args, **kwargs):
    if name == "pymupdf":
      raise ImportError("simulado")
    return real_import(name, *args, **kwargs)

  monkeypatch.setattr("builtins.__import__", fake_import)
  assert provenance.pymupdf_state() == {"available": False}


def test_git_state_flags_a_dirty_tree(monkeypatch):
  monkeypatch.setattr(provenance, "_run", lambda *a: "M um\nM dois")
  state = provenance.git_state()
  assert state["dirty"] is True
  assert state["dirty_files"] == 2
