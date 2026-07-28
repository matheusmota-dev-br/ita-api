"""Os últimos ramos: ordenação, rótulo em duas linhas, sessão HTTP e legados.

Vários destes só acontecem com um layout específico do corpus — "Questão" numa
linha e o número na seguinte — ou com uma combinação de parâmetros que a
interface não usa mas a API aceita. São os cantos onde ninguém olha.
"""

import json

import pymupdf
import pytest

from src.constants import Discipline, TestType
from src.etl.extract._download_pdfs_from_jsons import (
  RETRY_TOTAL,
  DownloadReport,
  _build_session,
)
from src.etl.load.upserts import png_size
from src.etl.transform.answer_keys import parse_answer_key
from src.etl.transform.labels import detect_question_positions
from src.etl.transform.main import run_transform
from src.etl.transform.naming import ExamRef
from src.etl.transform.positions import find_question_positions
from src.models.enums import DocumentKind, Phase

# O rótulo em duas linhas: a diagramação quebra "Questão" do número, e o
# detector precisa olhar a linha seguinte. Sem isso a questão passa batida.
SPLIT_LABEL = """Questão
12.
Enunciado da décima segunda, com texto suficiente para ter altura.

Questão
13.
Enunciado da décima terceira.
"""


def _pdf(tmp_path, body, name="doc.pdf", width=595, height=842):
  doc = pymupdf.open()
  page = doc.new_page(width=width, height=height)
  page.insert_textbox(
    pymupdf.Rect(50, 50, width - 50, height - 50), body, fontsize=11, lineheight=1.6
  )
  path = tmp_path / name
  doc.save(str(path))
  doc.close()
  return path


# --- rótulo quebrado em duas linhas ----------------------------------------


def test_number_on_the_following_line_is_detected(tmp_path):
  doc = pymupdf.open(_pdf(tmp_path, SPLIT_LABEL))
  numbers = sorted(n for n, _p, _r in find_question_positions(doc))
  assert numbers == [12, 13], "o número na linha seguinte tem que ser lido"
  doc.close()


def test_split_label_also_works_through_the_label_detector(tmp_path):
  doc = pymupdf.open(_pdf(tmp_path, SPLIT_LABEL))
  positions, style = detect_question_positions(doc, max_questions=300)
  assert sorted(n for n, _p, _r in positions) == [12, 13]
  assert style
  doc.close()


def test_number_beyond_the_ceiling_on_the_next_line_is_ignored(tmp_path):
  doc = pymupdf.open(_pdf(tmp_path, "Questão\n999.\nEnunciado."))
  assert find_question_positions(doc, max_questions=60) == []
  doc.close()


# --- ordenação -------------------------------------------------------------


async def test_order_year_asc(api):
  async with api as client:
    body = (await client.get("/api/v1/questions?order=year_asc")).json()
    assert [i["number"] for i in body["items"]] == [1, 2, 3]


async def test_order_random_returns_everything_eventually(api):
  """`order=random` é `ORDER BY RANDOM()`: muda a ordem, não o conjunto."""
  async with api as client:
    seen = set()
    for _ in range(8):
      body = (await client.get("/api/v1/questions?order=random")).json()
      assert body["total"] == 3
      seen.update(i["number"] for i in body["items"])
    assert seen == {1, 2, 3}


async def test_order_relevance_requires_a_query(api):
  async with api as client:
    assert (await client.get("/api/v1/questions?order=relevance")).status_code == 422


async def test_order_relevance_uses_the_bm25_ranking(api):
  """A ordem vem do FTS5 e é reproduzida em Python, não em SQL."""
  async with api as client:
    body = (await client.get("/api/v1/questions?q=complexo&order=relevance")).json()
    assert body["total"] >= 1
    assert body["items"][0]["excerpt"], "o trecho destacado acompanha a busca"


async def test_relevance_paginates(api):
  async with api as client:
    body = (
      await client.get(
        "/api/v1/questions?q=numero%20OR%20aceleracao%20OR%20funcao"
        "&order=relevance&page=1&page_size=1"
      )
    ).json()
    assert len(body["items"]) <= 1


# --- exames ----------------------------------------------------------------


@pytest.mark.parametrize(
  "query",
  ["", "?test_type=vestibular", "?year=2025", "?phase=1f", "?discipline=matematica"],
)
async def test_exam_filters(api, query):
  async with api as client:
    response = await client.get(f"/api/v1/exams{query}")
    assert response.status_code == 200


# --- sessão HTTP do downloader ---------------------------------------------


def test_build_session_configures_retries():
  """Retry com backoff é o que torna o download de 158 arquivos confiável."""
  session = _build_session()
  try:
    adapter = session.get_adapter("https://vestibular.ita.br")
    assert adapter.max_retries.total == RETRY_TOTAL
    assert adapter.max_retries.backoff_factor > 0
    assert session.get_adapter("http://exemplo") is not None
  finally:
    session.close()


def test_report_is_successful_only_without_failures():
  report = DownloadReport()
  assert report.ok
  report.failed.append({"filename": "x.pdf", "url": "u", "error": "404"})
  assert not report.ok


def test_report_summary_mentions_the_counts():
  report = DownloadReport()
  report.downloaded.extend(["a.pdf", "b.pdf"])
  report.skipped.append("c.pdf")
  report.failed.append({"filename": "d.pdf", "url": "u", "error": "e"})
  summary = report.summary()
  for number in ("2", "1"):
    assert number in summary


# --- tamanho de PNG --------------------------------------------------------


def test_png_size_reads_the_header(tmp_path):
  doc = pymupdf.open()
  page = doc.new_page(width=200, height=100)
  png = tmp_path / "c.png"
  png.write_bytes(page.get_pixmap(dpi=72).tobytes("png"))
  doc.close()

  size = png_size(png)
  assert size is not None
  assert size[0] > 0 and size[1] > 0


def test_png_size_of_a_non_png_is_none(tmp_path):
  fake = tmp_path / "nao.png"
  fake.write_bytes(b"isto nao e png, mas tem mais de vinte e quatro bytes aqui")
  assert png_size(fake) is None


def test_png_size_of_a_truncated_file_is_none(tmp_path):
  short = tmp_path / "curto.png"
  short.write_bytes(b"\x89PNG\r\n\x1a\n")
  assert png_size(short) is None


def test_png_size_of_a_missing_file_is_none(tmp_path):
  assert png_size(tmp_path / "nao-existe.png") is None


# --- gabarito: linhas que não casam ---------------------------------------


def test_answer_key_ignores_lines_without_pairs(tmp_path):
  """Cabeçalho, rodapé e texto solto não podem virar resposta."""
  body = """GABARITO OFICIAL
Instituto Tecnologico de Aeronautica
Vestibular 2015

1 A
2 B

Observacao: a questao 3 foi anulada.
Pagina 1 de 1
"""
  doc = pymupdf.open(_pdf(tmp_path, body))
  answers = parse_answer_key(doc)
  numbers = {a.number for a in answers}
  assert {1, 2} <= numbers
  # "Pagina 1 de 1" e "Vestibular 2015" não podem gerar respostas.
  assert 2015 not in numbers
  doc.close()


def test_answer_key_of_an_empty_document(tmp_path):
  doc = pymupdf.open(_pdf(tmp_path, "Sem nada aproveitavel."))
  assert parse_answer_key(doc) == []
  doc.close()


# --- process_pdf (usado pelo subcomando single e pelo notebook) ------------


def test_run_transform_exports_everything(tmp_path):
  """A função de um documento só, com todos os diretórios derivados da ref."""
  path = _pdf(
    tmp_path,
    "Questão 1. Primeira questao com texto.\n\nQuestão 2. Segunda questao.",
    name="prova_ita_vestibular_2016_fisica.pdf",
  )
  ref = ExamRef(
    test_type=TestType.VESTIBULAR,
    year=2016,
    phase=Phase.UNICA,
    discipline=Discipline.FISICA,
    kind=DocumentKind.PROVA,
    source_column="fisica",
    format_index=1,
    path=path,
  )
  doc = pymupdf.open(path)
  result = run_transform(doc, exam_ref=ref, out_root=tmp_path / "out")
  doc.close()

  assert sorted(n for n, _p, _r in result["positions"]) == [1, 2]
  assert result["question_rects"]
  assert result["questions_figs_dir"].exists()
  assert result["texts_dir"].exists()
  assert list(result["questions_figs_dir"].glob("*.png"))


def test_run_transform_can_skip_exports(tmp_path):
  path = _pdf(tmp_path, "Questão 1. Uma questao.")
  doc = pymupdf.open(path)
  result = run_transform(
    doc,
    exam_ref=None,
    out_root=tmp_path / "out",
    questions_figs_dir=tmp_path / "figs",
    non_questions_figs_dir=tmp_path / "fora",
    texts_dir=tmp_path / "textos",
    regions_pdf_path=tmp_path / "regioes.pdf",
    export_question_images=False,
    export_non_question_images=False,
    export_texts=False,
  )
  doc.close()
  assert result["positions"]
  assert "questions_figs_dir" not in result


# --- enrich: saídas do CLI -------------------------------------------------


def test_enrich_says_nothing_to_do_when_all_cached(tmp_path, monkeypatch, capsys):
  from src.etl.enrich import main as enrich_main

  class Job:
    slug = "x"
    number = 1
    source_sha256 = "s"
    images = [tmp_path / "i.png"]
    raw_text = "t"
    support_text = ""
    target = tmp_path / "out.json"

  (tmp_path / "i.png").write_bytes(b"\x89PNG\r\n\x1a\n")
  monkeypatch.setattr("src.config.settings.cache_dir", tmp_path / "cache")
  monkeypatch.setattr("src.etl.enrich.cache.CACHE_ROOT", tmp_path / "cache" / "enrich")
  monkeypatch.setattr(enrich_main, "collect_jobs", lambda only=None: [Job()])
  # Sem substituir estes dois, o `--dry-run` constrói um cliente real e o
  # `count_tokens` vira uma requisição de rede.
  monkeypatch.setattr(enrich_main, "build_client", lambda: object())
  monkeypatch.setattr(enrich_main, "count_input_tokens", lambda *a, **k: 900)
  monkeypatch.setattr("sys.argv", ["enrich", "--dry-run"])

  enrich_main.main()
  assert capsys.readouterr().out


def test_enrich_verbose_prints_each_question(tmp_path, monkeypatch, capsys):
  """Sem a linha por questão, um lote de 700 é uma barra de progresso muda."""
  from src.etl.enrich import main as enrich_main

  directory = tmp_path / "processed" / "slug"
  (directory / "questoes").mkdir(parents=True)
  image = directory / "questoes" / "questao_01.png"
  image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 40)
  (directory / "manifest.json").write_text(
    json.dumps(
      {
        "slug": "slug",
        "questions": [
          {
            "number": 1,
            "source_sha256": "sha1",
            "parts": [{"part_index": 0, "image": "questoes/questao_01.png"}],
          }
        ],
      }
    ),
    encoding="utf-8",
  )

  monkeypatch.setattr("src.config.settings.cache_dir", tmp_path / "cache")
  monkeypatch.setattr("src.etl.enrich.cache.CACHE_ROOT", tmp_path / "cache" / "enrich")

  from src.etl.load.reader import iter_processed

  processed_root = tmp_path / "processed"
  monkeypatch.setattr(
    enrich_main, "iter_processed", lambda root=None: iter_processed(processed_root)
  )

  class Result:
    usage = {"input_tokens": 10, "output_tokens": 20}
    status = "ok"
    error = None

    class question:  # noqa: N801 - duplo mínimo
      @staticmethod
      def model_dump(**_kwargs):
        return {
          "statement_markdown": "x",
          "transcription_confidence": "low",
          "alternatives": [],
        }

  monkeypatch.setattr(enrich_main, "build_client", lambda: object())
  monkeypatch.setattr(enrich_main, "normalize_question", lambda *a, **k: Result())

  enrich_main.run_enrich(verbose=True)
  out = capsys.readouterr().out
  assert "confianca=low" in out
  assert "chamadas" in out


def test_enrich_reports_a_failed_question_when_verbose(tmp_path, monkeypatch, capsys):
  from src.etl.enrich import main as enrich_main

  directory = tmp_path / "processed" / "slug"
  (directory / "questoes").mkdir(parents=True)
  png = directory / "questoes" / "questao_01.png"
  png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 40)
  (directory / "manifest.json").write_text(
    json.dumps(
      {
        "slug": "slug",
        "questions": [
          {
            "number": 1,
            "source_sha256": "sha1",
            "parts": [{"part_index": 0, "image": "questoes/questao_01.png"}],
          }
        ],
      }
    ),
    encoding="utf-8",
  )
  monkeypatch.setattr("src.config.settings.cache_dir", tmp_path / "cache")
  monkeypatch.setattr("src.etl.enrich.cache.CACHE_ROOT", tmp_path / "cache" / "enrich")

  from src.etl.load.reader import iter_processed

  processed_root = tmp_path / "processed"
  monkeypatch.setattr(
    enrich_main, "iter_processed", lambda root=None: iter_processed(processed_root)
  )

  class Failed:
    question = None
    usage = {}
    status = "refused"
    error = "classificadores recusaram"

  monkeypatch.setattr(enrich_main, "build_client", lambda: object())
  monkeypatch.setattr(enrich_main, "normalize_question", lambda *a, **k: Failed())

  stats = enrich_main.run_enrich(verbose=True)
  assert stats.failed == 1
  assert "FALHA" in capsys.readouterr().out
