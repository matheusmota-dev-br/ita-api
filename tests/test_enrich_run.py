"""Orquestração do enrich, com um cliente falso.

**Nenhum teste aqui chama a API.** `build_client` e `normalize_question` são
substituídos, e um dos testes prova que o caminho totalmente cacheado nem tenta
construir o cliente — que é a razão de reexecutar não custar nada.

O que se verifica é onde o dinheiro é gasto ou economizado: o cache, o `--limit`,
o teto de custo, e o que acontece quando uma questão falha.
"""

import json
from dataclasses import dataclass, field

import pytest

from src.etl.enrich.cache import EnrichCache, cache_key
from src.etl.enrich.main import EnrichStats, collect_jobs, run_enrich
from src.etl.enrich.prompt import PROMPT_VERSION

PAYLOAD = {
  "question_number": 1,
  "question_type": "objective",
  "statement_markdown": "Seja $z$ um complexo.",
  "alternatives": [{"letter": "A", "text_markdown": "$1$", "is_figure_only": False}],
  "detected_discipline": "matematica",
  "detected_topics": ["numeros-complexos"],
  "has_figure": False,
  "figure_descriptions": [],
  "transcription_confidence": "high",
  "notes": "",
}


@dataclass
class FakeQuestion:
  """Stand-in do modelo Pydantic: só precisa saber virar dicionário."""

  payload: dict = field(default_factory=lambda: dict(PAYLOAD))

  def model_dump(self, **_kwargs):
    return self.payload


@dataclass
class FakeResult:
  """Espelha `LLMResult` campo a campo — um duplo com outra forma não testa nada."""

  question: FakeQuestion | None = field(default_factory=FakeQuestion)
  usage: dict = field(
    default_factory=lambda: {"input_tokens": 100, "output_tokens": 200}
  )
  status: str = "ok"
  error: str | None = None


class Recorder:
  """Conta as chamadas que *teriam* ido para a API."""

  def __init__(self, results=None):
    self.results = list(results or [])
    self.calls = 0
    self.built = 0

  def build_client(self):
    self.built += 1
    return object()

  def normalize_question(self, _client, **kwargs):
    self.calls += 1
    if self.results:
      return self.results.pop(0)
    return FakeResult()


@pytest.fixture
def processed(tmp_path, monkeypatch):
  """Um documento processado com três questões e recortes em disco."""
  directory = tmp_path / "processed" / "vestibular-2025-1f"
  (directory / "questoes").mkdir(parents=True)
  (directory / "textos").mkdir(parents=True)

  questions = []
  for number in (1, 2, 3):
    image = directory / "questoes" / f"questao_{number:02d}.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 40)
    text = directory / "textos" / f"questao_{number:02d}_dentro.txt"
    text.write_text(f"Questao {number}. Enunciado.", encoding="utf-8")
    questions.append(
      {
        "number": number,
        "source_sha256": f"sha{number}",
        "parts": [{"part_index": 0, "image": f"questoes/questao_{number:02d}.png"}],
        "text_inside": f"textos/questao_{number:02d}_dentro.txt",
      }
    )

  manifest = {"slug": "vestibular-2025-1f", "questions": questions}
  (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

  monkeypatch.setattr("src.config.settings.cache_dir", tmp_path / "cache")
  monkeypatch.setattr("src.etl.enrich.cache.CACHE_ROOT", tmp_path / "cache" / "enrich")

  from src.etl.load.reader import iter_processed

  monkeypatch.setattr(
    "src.etl.enrich.main.iter_processed",
    lambda root=None: iter_processed(tmp_path / "processed"),
  )
  return directory


@pytest.fixture
def recorder(monkeypatch):
  rec = Recorder()
  monkeypatch.setattr("src.etl.enrich.main.build_client", rec.build_client)
  monkeypatch.setattr("src.etl.enrich.main.normalize_question", rec.normalize_question)
  return rec


# --- coleta ----------------------------------------------------------------


def test_collect_jobs_reads_the_manifest(processed):
  jobs = collect_jobs()
  assert [job.number for job in jobs] == [1, 2, 3]
  assert all(job.images for job in jobs)
  assert jobs[0].raw_text.startswith("Questao 1")


def test_collect_jobs_filters_by_slug(processed):
  assert collect_jobs("vestibular-2025") != []
  assert collect_jobs("nao-existe") == []


def test_collect_jobs_drops_missing_images(processed):
  """Um recorte referenciado no manifest mas ausente não pode virar chamada."""
  (processed / "questoes" / "questao_02.png").unlink()
  jobs = {job.number: job for job in collect_jobs()}
  assert jobs[2].images == []


# --- execução --------------------------------------------------------------


def test_run_writes_one_file_per_question(processed, recorder):
  stats = run_enrich(verbose=False)
  assert recorder.calls == 3
  assert stats.processed == 3
  written = sorted((processed / "enriquecido").glob("*.json"))
  assert len(written) == 3
  payload = json.loads(written[0].read_text(encoding="utf-8"))
  assert payload["statement_markdown"] == "Seja $z$ um complexo."


def test_second_run_is_free(processed, recorder):
  """O ponto do cache: reexecutar não gasta e não chama nem o construtor."""
  run_enrich(verbose=False)
  recorder.calls = 0
  recorder.built = 0

  stats = run_enrich(verbose=False)
  assert recorder.calls == 0, "nada deveria ter ido para a API"
  assert recorder.built == 0, "nem o cliente deveria ser construido"
  assert stats.cached == 3
  assert stats.processed == 0


def test_force_ignores_the_cache(processed, recorder):
  run_enrich(verbose=False)
  recorder.calls = 0
  run_enrich(force=True, verbose=False)
  assert recorder.calls == 3


def test_limit_caps_the_calls(processed, recorder):
  stats = run_enrich(limit=2, verbose=False)
  assert recorder.calls == 2
  assert stats.processed == 2


def test_only_filters_the_document(processed, recorder):
  run_enrich(only="nao-existe", verbose=False)
  assert recorder.calls == 0


def test_questions_without_image_are_skipped(processed, recorder):
  (processed / "questoes" / "questao_01.png").unlink()
  stats = run_enrich(verbose=False)
  assert stats.skipped_no_image == 1
  assert recorder.calls == 2


def test_prompt_version_change_invalidates_the_cache(processed, recorder, monkeypatch):
  """Bumpar o prompt tem que recobrar tudo, sem apagar arquivo nenhum."""
  run_enrich(verbose=False)
  recorder.calls = 0

  monkeypatch.setattr("src.etl.enrich.main.PROMPT_VERSION", "999")
  run_enrich(verbose=False)
  assert recorder.calls == 3


def test_failure_is_recorded_and_not_retried(processed, monkeypatch):
  """Uma questão que falhou não pode ser recobrada em cada execução."""
  rec = Recorder(
    results=[
      FakeResult(question=None, status="refusal", error="recusou transcrever"),
      FakeResult(),
      FakeResult(),
    ]
  )
  monkeypatch.setattr("src.etl.enrich.main.build_client", rec.build_client)
  monkeypatch.setattr("src.etl.enrich.main.normalize_question", rec.normalize_question)

  first = run_enrich(verbose=False)
  assert first.failed == 1
  assert first.processed == 2

  rec.calls = 0
  second = run_enrich(verbose=False)
  assert rec.calls == 0, "o erro esta em cache; nao deve ser tentado de novo"
  assert second.failed == 1


def test_retry_failed_tries_again(processed, monkeypatch):
  rec = Recorder(results=[FakeResult(question=None, status="error", error="timeout")])
  monkeypatch.setattr("src.etl.enrich.main.build_client", rec.build_client)
  monkeypatch.setattr("src.etl.enrich.main.normalize_question", rec.normalize_question)
  run_enrich(limit=1, verbose=False)

  rec.calls = 0
  rec.results = [FakeResult()]
  run_enrich(retry_failed=True, limit=1, verbose=False)
  assert rec.calls == 1


def test_cost_ceiling_stops_the_run(processed, monkeypatch, capsys):
  """O teto existe para que um erro de estimativa não vire uma fatura."""
  rec = Recorder(
    results=[
      FakeResult(usage={"input_tokens": 10_000_000, "output_tokens": 10_000_000})
      for _ in range(3)
    ]
  )
  monkeypatch.setattr("src.etl.enrich.main.build_client", rec.build_client)
  monkeypatch.setattr("src.etl.enrich.main.normalize_question", rec.normalize_question)

  run_enrich(max_cost=0.01, verbose=False)
  assert rec.calls == 1, "depois de estourar o teto nao pode haver outra chamada"
  assert "teto de custo" in capsys.readouterr().err


def test_cache_key_matches_what_the_run_stores(processed, recorder):
  from src.config import settings

  run_enrich(verbose=False)
  cache = EnrichCache()
  key = cache_key("sha1", PROMPT_VERSION, settings.llm_model)
  assert cache.get(key) is not None


def test_stats_summary_mentions_each_bucket():
  stats = EnrichStats()
  stats.processed, stats.cached, stats.failed, stats.skipped_no_image = 5, 2, 1, 3
  summary = stats.summary()
  for number in ("5", "2", "1", "3"):
    assert number in summary


def test_dry_run_does_not_call_the_api(processed, monkeypatch, capsys):
  """`--dry-run` estima o custo; chamar a API nele seria o oposto do objetivo."""
  from src.etl.enrich.main import main

  called = []
  monkeypatch.setattr(
    "src.etl.enrich.main.normalize_question",
    lambda *a, **k: called.append(1),
  )
  # A contagem de tokens de entrada usa o cliente; um duplo basta.
  monkeypatch.setattr("src.etl.enrich.main.build_client", lambda: object())
  monkeypatch.setattr("src.etl.enrich.main.count_input_tokens", lambda *a, **k: 800)
  monkeypatch.setattr("sys.argv", ["enrich", "--dry-run"])

  assert main() == 0
  assert called == [], "dry-run nao pode chamar a API"
  assert capsys.readouterr().out
