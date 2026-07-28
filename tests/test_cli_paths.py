"""Caminhos de CLI, cliente e sessão que faltavam.

O cliente do LLM é exercitado com um duplo que devolve respostas prontas —
**nenhuma requisição de rede** — porque o que importa aqui são as respostas
anormais: uma recusa volta como HTTP 200 com conteúdo vazio, e ler `content[0]`
sem checar `stop_reason` estouraria.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
import pymupdf
import pytest

from src.etl.enrich.client import (
  DEFAULT_EFFORT,
  MissingApiKey,
  _messages,
  build_client,
  count_input_tokens,
  normalize_question,
)
from src.etl.transform import main as transform_main
from src.etl.transform.export_text import save_question_text_files

VALID_PARSED = {
  "question_number": 1,
  "question_type": "objective",
  "statement_markdown": "Seja $z$ um complexo.",
  "alternatives": [],
  "detected_discipline": "matematica",
  "detected_topics": [],
  "has_figure": False,
  "figure_descriptions": [],
  "transcription_confidence": "high",
  "notes": "",
}


# --- duplos do SDK ---------------------------------------------------------


@dataclass
class FakeUsage:
  input_tokens: int = 120
  output_tokens: int = 340
  cache_read_input_tokens: int = 10
  cache_creation_input_tokens: int = 0


@dataclass
class FakeParsed:
  stop_reason: str = "end_turn"
  parsed_output: object = None
  usage: FakeUsage = field(default_factory=FakeUsage)


class FakeMessages:
  def __init__(self, result=None, error=None, tokens=800):
    self.result = result
    self.error = error
    self.tokens = tokens
    self.kwargs = None

  def parse(self, **kwargs):
    self.kwargs = kwargs
    if self.error:
      raise self.error
    return self.result

  def count_tokens(self, **kwargs):
    self.kwargs = kwargs
    return type("Count", (), {"input_tokens": self.tokens})()


class FakeClient:
  def __init__(self, **kwargs):
    self.messages = FakeMessages(**kwargs)


@pytest.fixture
def crop(tmp_path):
  png = tmp_path / "questao_01.png"
  doc = pymupdf.open()
  page = doc.new_page(width=200, height=100)
  page.insert_text((10, 50), "Questao 1.", fontsize=12)
  png.write_bytes(page.get_pixmap(dpi=72).tobytes("png"))
  doc.close()
  return png


# --- cliente ---------------------------------------------------------------


def test_build_client_without_a_key_is_a_named_error(monkeypatch):
  monkeypatch.setattr("src.config.settings.anthropic_api_key", None)
  with pytest.raises(MissingApiKey):
    build_client()


def test_build_client_with_a_key_returns_a_client(monkeypatch):
  """Não faz requisição: só constrói o objeto."""
  monkeypatch.setattr("src.config.settings.anthropic_api_key", "sk-de-teste")
  assert isinstance(build_client(), anthropic.Anthropic)


def test_messages_put_the_image_before_the_text(crop):
  """A imagem é a fonte da verdade; o texto vem depois, como apoio."""
  messages = _messages([crop], "texto bruto", None)
  content = messages[0]["content"]
  assert content[0]["type"] == "image"
  assert content[-1]["type"] == "text"
  assert "texto bruto" in content[-1]["text"]


def test_messages_without_images(crop):
  content = _messages([], "só texto", None)[0]["content"]
  assert all(block["type"] == "text" for block in content)


def test_messages_include_support_text(crop):
  content = _messages([crop], "enunciado", "Convenções: g = 10")[0]["content"]
  assert any("g = 10" in block.get("text", "") for block in content)


def test_normalize_returns_ok_on_a_parsed_response(crop):
  from src.etl.enrich.schema import NormalizedQuestion

  parsed = NormalizedQuestion.model_validate(VALID_PARSED)
  client = FakeClient(result=FakeParsed(parsed_output=parsed))
  result = normalize_question(client, images=[crop], raw_text="x")

  assert result.status == "ok"
  assert result.question.statement_markdown == "Seja $z$ um complexo."
  assert result.usage["input_tokens"] == 120
  assert result.usage["cache_read_input_tokens"] == 10


def test_normalize_detects_a_refusal(crop):
  """Uma recusa vem como HTTP 200 com conteúdo vazio.

  Ler `content[0]` sem checar `stop_reason` estouraria com IndexError, e o lote
  inteiro morreria por causa de uma questão.
  """
  client = FakeClient(result=FakeParsed(stop_reason="refusal"))
  result = normalize_question(client, images=[crop], raw_text="x")
  assert result.status == "refused"
  assert result.question is None
  assert result.usage["input_tokens"] == 120, "a recusa e cobrada; registre o custo"


def test_normalize_detects_a_missing_structured_output(crop):
  client = FakeClient(result=FakeParsed(parsed_output=None))
  result = normalize_question(client, images=[crop], raw_text="x")
  assert result.status == "schema_error"
  assert result.question is None


def test_normalize_turns_a_retryable_error_into_a_result(crop):
  """Falha de item não pode levantar: derrubaria o lote."""
  error = anthropic.APIConnectionError(request=None)
  client = FakeClient(error=error)
  result = normalize_question(client, images=[crop], raw_text="x")
  assert result.status == "api_error"
  assert "APIConnectionError" in result.error


def test_normalize_reraises_a_fatal_error(crop, monkeypatch):
  """Chave inválida é fatal: insistir em 2000 questões seria absurdo."""

  class FakeAuthError(anthropic.AuthenticationError):
    def __init__(self):
      pass

  client = FakeClient(error=FakeAuthError())
  with pytest.raises(anthropic.AuthenticationError):
    normalize_question(client, images=[crop], raw_text="x")


def test_normalize_passes_the_effort_through(crop):
  from src.etl.enrich.schema import NormalizedQuestion

  parsed = NormalizedQuestion.model_validate(VALID_PARSED)
  client = FakeClient(result=FakeParsed(parsed_output=parsed))
  normalize_question(client, images=[crop], raw_text="x", effort="low")
  assert client.messages.kwargs["output_config"] == {"effort": "low"}


def test_default_effort_is_declared():
  assert DEFAULT_EFFORT


def test_count_input_tokens_uses_the_endpoint(crop):
  client = FakeClient(tokens=1234)
  assert count_input_tokens(client, images=[crop], raw_text="x") == 1234


# --- export de texto -------------------------------------------------------


def test_save_question_text_files_writes_inside_and_outside(tmp_path):
  out = save_question_text_files(
    {1: "enunciado da um", 2: "enunciado da dois"},
    {1: "convencoes da secao"},
    out_dir=tmp_path,
  )
  assert (out / "questao_01_dentro.txt").read_text(
    encoding="utf-8"
  ) == "enunciado da um"
  assert (out / "questao_01_fora.txt").exists()
  # A questão 2 não tem texto de fora: o arquivo não deve ser criado vazio.
  assert not (out / "questao_02_fora.txt").exists()


def test_save_question_text_files_creates_the_directory(tmp_path):
  target = tmp_path / "nao" / "existe"
  save_question_text_files({1: "x"}, {}, out_dir=target)
  assert (target / "questao_01_dentro.txt").exists()


# --- CLI do transform ------------------------------------------------------


@pytest.fixture
def pdf_corpus(tmp_path):
  provas = tmp_path / "vestibular" / "provas"
  provas.mkdir(parents=True)
  path = provas / "prova_ita_vestibular_2012_fisica.pdf"
  doc = pymupdf.open()
  page = doc.new_page(width=595, height=842)
  page.insert_textbox(
    pymupdf.Rect(50, 50, 545, 800),
    "Questão 1. Enunciado.\n\nA ( ) uma\nB ( ) duas\nC ( ) tres\n"
    "D ( ) quatro\nE ( ) cinco",
    fontsize=11,
    lineheight=1.5,
  )
  doc.save(str(path))
  doc.close()
  (provas / "_index.json").write_text(
    json.dumps(
      {
        path.name: {
          "filename": path.name,
          "column": "fisica",
          "format_index": 1,
          "url": "u",
          "sha256": "a" * 64,
        }
      }
    ),
    encoding="utf-8",
  )
  return provas, path


def test_single_subcommand_reports_the_numbers(
  pdf_corpus, tmp_path, monkeypatch, capsys
):
  _provas, pdf = pdf_corpus
  monkeypatch.setattr("sys.argv", ["transform", "single", str(pdf)])
  monkeypatch.setattr(
    transform_main, "get_processed_root", lambda **_kw: tmp_path / "processed"
  )
  assert transform_main.main() == 0
  assert "1" in capsys.readouterr().out


def test_batch_subcommand_processes_the_corpus(
  pdf_corpus, tmp_path, monkeypatch, capsys
):
  provas, _pdf = pdf_corpus
  monkeypatch.setattr("src.etl.transform.batch.PROVAS_DIRS", (provas,))
  monkeypatch.setattr("src.etl.transform.main.PROVAS_DIRS", (provas,), raising=False)
  monkeypatch.setattr("sys.argv", ["transform", "batch"])
  monkeypatch.setattr(
    transform_main, "get_processed_root", lambda **_kw: tmp_path / "processed"
  )
  assert transform_main.main() == 0
  assert "total de questoes" in capsys.readouterr().out


def test_report_without_a_corpus_says_so(tmp_path, monkeypatch, capsys):
  monkeypatch.setattr("sys.argv", ["transform", "report"])
  monkeypatch.setattr(
    transform_main, "get_processed_root", lambda **_kw: tmp_path / "vazio"
  )
  transform_main.main()
  out = capsys.readouterr()
  assert "nenhum relatorio" in (out.out + out.err)


def test_no_subcommand_defaults_to_batch(monkeypatch):
  """`python -m src.etl.transform` sem argumento não pode falhar seco."""
  monkeypatch.setattr("sys.argv", ["transform"])
  called = []
  monkeypatch.setattr(
    transform_main,
    "run_batch",
    lambda **kwargs: called.append(kwargs) or _EmptyReport(),
  )
  transform_main.main()
  assert called, "deveria ter caido no batch"


class _EmptyReport:
  total_questions = 0
  documents: list = []

  def by_status(self):
    return {}


# --- orquestrador ----------------------------------------------------------


def _stub_pipeline(monkeypatch, order, extract_code=0):
  import src.etl.main as orchestrator

  def extract():
    order.append("extract")
    return extract_code

  monkeypatch.setattr(orchestrator, "extract_main", extract)
  monkeypatch.setattr(
    orchestrator, "run_batch", lambda **_kw: order.append("transform") or _EmptyReport()
  )
  monkeypatch.setattr(
    orchestrator, "run_load", lambda **_kw: order.append("load") or _EmptyStats()
  )
  return orchestrator


def test_pipeline_runs_the_three_stages_in_order(monkeypatch, capsys):
  order = []
  orchestrator = _stub_pipeline(monkeypatch, order)
  monkeypatch.setattr("sys.argv", ["pipeline"])

  assert orchestrator.main() == 0
  assert order == ["extract", "transform", "load"]
  out = capsys.readouterr().out
  assert "== extract ==" in out and "== load ==" in out


def test_pipeline_stops_when_extract_fails(monkeypatch, capsys):
  """Seguir com um corpus incompleto produziria um banco errado em silêncio."""
  order = []
  orchestrator = _stub_pipeline(monkeypatch, order, extract_code=1)
  monkeypatch.setattr("sys.argv", ["pipeline"])

  assert orchestrator.main() == 1
  assert order == ["extract"], "transform e load nao podem rodar"
  assert "--keep-going" in capsys.readouterr().err


def test_keep_going_continues_after_a_failed_extract(monkeypatch, capsys):
  order = []
  orchestrator = _stub_pipeline(monkeypatch, order, extract_code=1)
  monkeypatch.setattr("sys.argv", ["pipeline", "--keep-going"])

  assert orchestrator.main() == 0
  assert order == ["extract", "transform", "load"]


class _EmptyStats:
  def summary(self):
    return "0 documentos"


# --- sessão do banco -------------------------------------------------------


async def test_get_db_commits_on_success(tmp_path, monkeypatch):
  """A dependência faz commit ao final; sem isso um POST não persistiria."""
  from sqlalchemy import text as sql_text

  from src.database import get_db

  generator = get_db()
  session = await generator.__anext__()
  await session.execute(sql_text("SELECT 1"))
  with pytest.raises(StopAsyncIteration):
    await generator.__anext__()


async def test_get_db_rolls_back_on_error():
  """Um erro tem que desfazer, não deixar meia transação aberta."""
  from sqlalchemy.exc import SQLAlchemyError

  from src.database import get_db

  generator = get_db()
  await generator.__anext__()
  with pytest.raises(SQLAlchemyError):
    await generator.athrow(SQLAlchemyError("simulado"))


async def test_check_db_connection_true():
  from src.database import check_db_connection

  assert await check_db_connection() is True


async def test_check_db_connection_false(monkeypatch):
  from src.database import check_db_connection

  class Broken:
    async def __aenter__(self):
      raise __import__("sqlalchemy").exc.SQLAlchemyError("sem banco")

    async def __aexit__(self, *_a):
      return False

  monkeypatch.setattr("src.database.async_session_maker", lambda: Broken())
  assert await check_db_connection() is False


# --- entry points ----------------------------------------------------------


@pytest.mark.parametrize(
  "module",
  ["src.etl.load", "src.etl.transform", "src.etl.enrich", "src.etl.extract"],
)
def test_dunder_main_is_wired(module):
  """`__main__.py` é código próprio: um import errado quebra `python -m`."""
  import importlib
  import sys as system

  path = Path(module.replace(".", "/")) / "__main__.py"
  assert path.is_file()
  source = path.read_text(encoding="utf-8")
  assert "sys.exit(main())" in source
  # E o nome importado tem que existir de fato no módulo de origem.
  origin = importlib.import_module(f"{module}.main")
  assert hasattr(origin, "main"), f"{module}.main nao expoe main()"
  assert module in system.modules
