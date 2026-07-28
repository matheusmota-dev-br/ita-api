"""Extract: downloader, índice e utilitários de plataforma.

**Nada aqui toca a rede.** A sessão HTTP é substituída por um duplo, e é isso
que permite testar o que importa neste estágio: que uma falha não seja engolida
(o bug original era `except RequestException: pass`, que fazia um corpus parcial
ficar indistinguível de um completo) e que o `_index.json` preserve de qual das
três tabelas históricas do site cada arquivo veio.
"""

import hashlib
import json
from pathlib import Path

import pytest
import requests

from src.constants import Architecture, OperatingSystem
from src.etl.extract._download_pdfs_from_jsons import (
  INDEX_FILENAME,
  REPORT_FILENAME,
  DownloadReport,
  download_pdfs_from_jsons,
  load_index,
)
from src.etl.extract._json_file_exist import json_files_exist
from src.utils.get_arch import get_arch
from src.utils.get_os import get_os

PDF_BYTES = b"%PDF-1.4\n%%EOF\n"


class FakeResponse:
  def __init__(self, content=PDF_BYTES, error=None):
    self.content = content
    self._error = error

  def raise_for_status(self):
    if self._error:
      raise self._error


class FakeSession:
  """Sessão HTTP de mentira: devolve o que o teste mandar, por URL."""

  def __init__(self, responses: dict[str, FakeResponse] | None = None, default=None):
    self.responses = responses or {}
    self.default = default or FakeResponse()
    self.requested: list[str] = []
    self.closed = False

  def get(self, url, timeout=None):
    self.requested.append(url)
    response = self.responses.get(url, self.default)
    if isinstance(response, Exception):
      raise response
    return response

  def close(self):
    self.closed = True


@pytest.fixture
def scraped(tmp_path):
  """Dois JSONs raspados, um por era, como o Selenium os grava."""
  (tmp_path / "2tabela.json").write_text(
    json.dumps([{"ano": "2025", "prova_1f": "https://ita/2025_1f.pdf"}]),
    encoding="utf-8",
  )
  (tmp_path / "1tabela.json").write_text(
    json.dumps([{"ano": "2010", "fisica": "https://ita/2010_fisica.pdf"}]),
    encoding="utf-8",
  )
  return tmp_path


@pytest.fixture
def fake_session(monkeypatch):
  session = FakeSession()
  monkeypatch.setattr(
    "src.etl.extract._download_pdfs_from_jsons._build_session", lambda: session
  )
  return session


def _run(output_dir, **kwargs):
  return download_pdfs_from_jsons(
    output_dir=output_dir, n=2, json_prefix="tabela.json", **kwargs
  )


# --- download --------------------------------------------------------------


def test_downloads_and_writes_the_files(scraped, fake_session):
  report = _run(scraped)
  assert sorted(report.downloaded) == [
    "prova_ita_vestibular_2010_fisica.pdf",
    "prova_ita_vestibular_2025_prova_1f.pdf",
  ]
  assert (scraped / "provas" / "prova_ita_vestibular_2025_prova_1f.pdf").exists()
  assert fake_session.closed, "a sessao tem que ser fechada mesmo em caso de erro"


def test_index_records_the_era_of_each_file(scraped, fake_session):
  """O nome do arquivo joga fora de qual tabela o PDF veio.

  As três eras usam as mesmas colunas com significados diferentes: `fisica` é
  fase única em 2008–2018 e 2ª fase de 2019 em diante. Sem o `format_index` no
  índice, não há como recuperar isso — e adivinhar por faixa de ano quebraria no
  próximo redesenho do site.
  """
  _run(scraped)
  index = load_index(scraped / "provas")
  # i=0 é a primeira tabela (era mais recente) e recebe o maior format_index.
  assert index["prova_ita_vestibular_2025_prova_1f.pdf"]["format_index"] == 2
  assert index["prova_ita_vestibular_2010_fisica.pdf"]["format_index"] == 1
  assert index["prova_ita_vestibular_2010_fisica.pdf"]["column"] == "fisica"


def test_index_records_the_checksum_and_url(scraped, fake_session):
  _run(scraped)
  entry = load_index(scraped / "provas")["prova_ita_vestibular_2010_fisica.pdf"]
  assert entry["sha256"] == hashlib.sha256(PDF_BYTES).hexdigest()
  assert entry["url"] == "https://ita/2010_fisica.pdf"
  assert entry["byte_size"] == len(PDF_BYTES)


def test_failures_are_reported_not_swallowed(scraped, monkeypatch):
  """O bug original: `except RequestException: pass`.

  Um corpus parcial ficava indistinguível de um completo — nada no disco nem na
  saída dizia que faltavam arquivos.
  """
  session = FakeSession(
    responses={
      "https://ita/2010_fisica.pdf": requests.RequestException("404 no servidor do ITA")
    }
  )
  monkeypatch.setattr(
    "src.etl.extract._download_pdfs_from_jsons._build_session", lambda: session
  )
  report = _run(scraped)

  assert report.downloaded == ["prova_ita_vestibular_2025_prova_1f.pdf"]
  assert len(report.failed) == 1
  assert report.failed[0]["filename"] == "prova_ita_vestibular_2010_fisica.pdf"
  assert "404" in report.failed[0]["error"]
  # E o arquivo que falhou não pode ficar meio escrito no disco.
  assert not (scraped / "provas" / "prova_ita_vestibular_2010_fisica.pdf").exists()


def test_one_failure_does_not_abort_the_rest(scraped, monkeypatch):
  session = FakeSession(
    responses={"https://ita/2025_1f.pdf": requests.RequestException("timeout")}
  )
  monkeypatch.setattr(
    "src.etl.extract._download_pdfs_from_jsons._build_session", lambda: session
  )
  report = _run(scraped)
  assert report.downloaded == ["prova_ita_vestibular_2010_fisica.pdf"]
  assert len(report.failed) == 1


def test_second_run_skips_what_is_already_there(scraped, fake_session):
  """Reexecutar não pode rebaixar 158 PDFs."""
  _run(scraped)
  assert len(fake_session.requested) == 2

  second = FakeSession()
  import src.etl.extract._download_pdfs_from_jsons as module

  original = module._build_session
  module._build_session = lambda: second
  try:
    report = _run(scraped)
  finally:
    module._build_session = original

  assert sorted(report.skipped) == [
    "prova_ita_vestibular_2010_fisica.pdf",
    "prova_ita_vestibular_2025_prova_1f.pdf",
  ]
  assert second.requested == [], "nada deveria ter sido pedido de novo"


def test_force_redownloads(scraped, fake_session):
  _run(scraped)
  report = _run(scraped, force=True)
  assert len(report.downloaded) == 2
  assert report.skipped == []


def test_report_is_written_to_disk(scraped, monkeypatch):
  """O relatório em disco é o que distingue corpus completo de parcial."""
  session = FakeSession(
    responses={"https://ita/2025_1f.pdf": requests.RequestException("404")}
  )
  monkeypatch.setattr(
    "src.etl.extract._download_pdfs_from_jsons._build_session", lambda: session
  )
  _run(scraped)

  payload = json.loads((scraped / REPORT_FILENAME).read_text(encoding="utf-8"))
  assert payload["downloaded"] == ["prova_ita_vestibular_2010_fisica.pdf"]
  assert payload["failed"][0]["filename"] == "prova_ita_vestibular_2025_prova_1f.pdf"


def test_missing_json_is_skipped(tmp_path, fake_session):
  """Faltar uma das tabelas não é erro: o site nem sempre tem as três."""
  (tmp_path / "1tabela.json").write_text(
    json.dumps([{"ano": "2010", "fisica": "https://ita/x.pdf"}]), encoding="utf-8"
  )
  report = _run(tmp_path)
  assert len(report.downloaded) == 1


def test_non_url_cells_are_ignored(tmp_path, fake_session):
  """A tabela do site tem células vazias e textuais entre os links."""
  (tmp_path / "1tabela.json").write_text(
    json.dumps(
      [{"ano": "2010", "fisica": "", "quimica": "-", "ingles": "https://ita/i.pdf"}]
    ),
    encoding="utf-8",
  )
  report = _run(tmp_path)
  assert report.downloaded == ["prova_ita_vestibular_2010_ingles.pdf"]


def test_empty_row_is_ignored(tmp_path, fake_session):
  (tmp_path / "1tabela.json").write_text(json.dumps([{}, {}]), encoding="utf-8")
  assert _run(tmp_path).downloaded == []


def test_load_index_of_a_fresh_directory_is_empty(tmp_path):
  assert load_index(tmp_path) == {}


def test_index_filename_is_stable():
  """O transform depende deste nome para desambiguar as eras."""
  assert INDEX_FILENAME == "_index.json"


def test_report_counts_read_naturally():
  report = DownloadReport()
  report.downloaded.append("a.pdf")
  report.failed.append({"filename": "b.pdf", "url": "u", "error": "e"})
  report.skipped.append("c.pdf")
  assert (len(report.downloaded), len(report.failed), len(report.skipped)) == (1, 1, 1)


# --- helpers ---------------------------------------------------------------


def test_json_files_exist_requires_all_of_them(tmp_path):
  """Só é verdade quando as N tabelas estão em disco: raspar é tudo ou nada."""
  assert not json_files_exist(output_dir=tmp_path, n=2, json_prefix="tabela.json")
  (tmp_path / "1tabela.json").write_text("[]", encoding="utf-8")
  assert not json_files_exist(output_dir=tmp_path, n=2, json_prefix="tabela.json")
  (tmp_path / "2tabela.json").write_text("[]", encoding="utf-8")
  assert json_files_exist(output_dir=tmp_path, n=2, json_prefix="tabela.json")


# --- plataforma ------------------------------------------------------------


@pytest.mark.parametrize(
  ("machine", "expected"),
  [
    ("x86_64", Architecture.X64),
    ("AMD64", Architecture.X64),
    ("arm64", Architecture.ARM64),
    ("aarch64", Architecture.ARM64),
    ("mips", Architecture.X64),
  ],
)
def test_arch_detection(monkeypatch, machine, expected):
  monkeypatch.setattr("platform.machine", lambda: machine)
  assert get_arch() is expected


@pytest.mark.parametrize(
  ("system", "expected"),
  [
    ("Darwin", OperatingSystem.MACOS),
    ("Linux", OperatingSystem.LINUX),
    ("Windows", OperatingSystem.WINDOWS),
  ],
)
def test_os_detection(monkeypatch, system, expected):
  monkeypatch.setattr("platform.system", lambda: system)
  assert get_os() is expected


def test_unknown_os_is_none(monkeypatch):
  monkeypatch.setattr("platform.system", lambda: "Plan9")
  assert get_os() is None


def test_real_platform_is_recognised():
  """No ambiente onde os testes rodam, a detecção tem que funcionar."""
  assert get_os() is not None
  assert isinstance(get_arch(), Architecture)


def test_index_survives_a_roundtrip(tmp_path):
  """O índice é lido pelo transform; o formato tem que aguentar ida e volta."""
  provas = tmp_path / "provas"
  provas.mkdir()
  payload = {"a.pdf": {"filename": "a.pdf", "format_index": 3, "column": "fisica"}}
  (provas / INDEX_FILENAME).write_text(
    json.dumps(payload, ensure_ascii=False), encoding="utf-8"
  )
  assert load_index(provas) == payload


def test_index_of_a_path_that_is_not_a_directory(tmp_path):
  missing = tmp_path / "nao-existe"
  assert load_index(missing) == {}
  assert isinstance(missing, Path)
