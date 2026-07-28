"""Servidor e navegador de verdade, contra um ambiente descartável.

Estes testes sobem um uvicorn e dirigem um Chrome headless. O que eles exercitam
não dá para exercitar de outra forma: a seleção de recorte é geometria de mouse,
o preview de LaTeX é MathML renderizado pelo navegador, e o bug que motivou tudo
isso — o retângulo que sumia ao soltar o mouse — era uma corrida entre o
`useEffect` do React e o evento do sistema.

**O servidor roda num ambiente temporário, por variável de ambiente.** Um teste
de navegador já gravou `renumber`, `create` e `delete` no `decisions.json` de
verdade, que é versionado e é a fonte da auditoria. `REVIEW_DIR`, `DATABASE_URL`
e `MEDIA_ROOT` apontam para o tmp do teste, então não há como sujar o corpus.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.config import PROJECT_ROOT
from src.constants import Discipline, FormatType, TestType
from src.models import Alternative, Asset, Document, Exam, Question
from src.models.enums import AssetKind, DocumentKind, Phase, ReviewStatus
from src.models.question import QuestionPage
from tests.conftest import create_schema

pytest.importorskip("selenium", reason="selenium ausente")

# Sem Chrome não há o que dirigir. Skip em vez de falha: o CI roda sem navegador
# e a suíte de backend continua sendo o portão obrigatório.
CHROME = (
  shutil.which("chromedriver")
  or shutil.which("google-chrome")
  or shutil.which("chromium")
  or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
)
pytestmark = pytest.mark.skipif(
  not Path(CHROME).exists() and shutil.which("chromedriver") is None,
  reason="Chrome/chromedriver ausente",
)

PAGE_BODY = """Questão {n}. Enunciado da questão {n}, com texto suficiente para
que a extração tenha o que devolver e o recorte tenha altura real.

A ( ) primeira alternativa da questão {n}.
B ( ) segunda alternativa da questão {n}.
C ( ) terceira alternativa da questão {n}.
D ( ) quarta alternativa da questão {n}.
E ( ) quinta alternativa da questão {n}.
"""


def _free_port() -> int:
  with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    return sock.getsockname()[1]


def _seed(db_path: Path, pdf_path: Path, media_root: Path, *, schema=True) -> None:
  """Corpus mínimo: um documento com 4 questões.

  Reexecutável: apaga o que houver antes de inserir, para que um teste que
  exclui ou renumera não contamine o seguinte. O servidor fica de pé — cada
  requisição abre a própria sessão, então trocar as linhas por baixo é seguro.
  """
  engine = create_engine(f"sqlite:///{db_path}")
  if schema:
    create_schema(engine)
  else:
    with engine.connect() as conn:
      conn.exec_driver_sql("PRAGMA foreign_keys=ON")
      # A ordem importa: filhos antes dos pais, senão a FK recusa.
      for table in (
        "question_topics",
        "question_support_texts",
        "assets",
        "alternatives",
        "question_pages",
        "answer_key_entries",
        "questions",
        "support_texts",
        "documents",
        "exams",
      ):
        conn.exec_driver_sql(f"DELETE FROM {table}")
      conn.exec_driver_sql("DELETE FROM questions_fts")
      conn.commit()
  crops = media_root / "crops"
  crops.mkdir(parents=True, exist_ok=True)

  document_pdf = pymupdf.open(pdf_path)
  with Session(engine) as session:
    exam = Exam(test_type=TestType.VESTIBULAR, year=2025, phase=Phase.PRIMEIRA)
    session.add(exam)
    session.flush()
    document = Document(
      exam_id=exam.id,
      kind=DocumentKind.PROVA,
      source_column="prova_1f",
      discipline=Discipline.MATEMATICA,
      local_path=str(pdf_path),
      sha256="e2e",
      page_count=len(document_pdf),
      processed_dir="processed/e2e",
    )
    session.add(document)
    session.flush()

    for number in (1, 2, 3, 4):
      page_no = number - 1
      page = document_pdf[page_no]
      rect = pymupdf.Rect(50, 50, 545, 320)
      crop = page.get_pixmap(clip=rect, dpi=150, alpha=False)
      relative = f"crops/questao_{number:02d}.png"
      (media_root / relative).write_bytes(crop.tobytes("png"))

      question = Question(
        document_id=document.id,
        exam_id=exam.id,
        number=number,
        format_type=FormatType.OBJECTIVE,
        discipline=Discipline.MATEMATICA,
        statement_raw=page.get_text(clip=rect, sort=True).strip(),
        page_start=page_no,
        page_end=page_no,
        source_sha256=f"sha{number}",
        review_status=ReviewStatus.RAW,
      )
      question.pages.append(
        QuestionPage(
          part_index=0,
          page_no=page_no,
          x0=rect.x0,
          y0=rect.y0,
          x1=rect.x1,
          y1=rect.y1,
        )
      )
      question.assets.append(
        Asset(
          kind=AssetKind.QUESTION_CROP,
          relative_path=relative,
          page_no=page_no,
          sha256=f"img{number}",
        )
      )
      for index, letter in enumerate("ABCDE"):
        question.alternatives.append(
          Alternative(letter=letter, order_index=index, review_status=ReviewStatus.RAW)
        )
      session.add(question)
    session.commit()
  document_pdf.close()
  engine.dispose()


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
  """uvicorn num subprocesso, apontado para um ambiente isolado."""
  root = tmp_path_factory.mktemp("e2e")
  pdf_path = root / "prova.pdf"
  doc = pymupdf.open()
  for number in (1, 2, 3, 4):
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(
      pymupdf.Rect(50, 50, 545, 800),
      PAGE_BODY.format(n=number),
      fontsize=11,
      lineheight=1.5,
    )
  doc.save(str(pdf_path))
  doc.close()

  db = root / "e2e.db"
  media = root / "media"
  review = root / "review"
  review.mkdir()
  _seed(db, pdf_path, media)

  port = _free_port()
  env = {
    **os.environ,
    "DATABASE_URL": f"sqlite+aiosqlite:///{db}",
    "MEDIA_ROOT": str(media),
    "REVIEW_DIR": str(review),
    "DATA_DIR": str(root),
    "PYTHONPATH": str(PROJECT_ROOT),
    # Impede que uma chave presente no .env seja usada por acidente.
    "ANTHROPIC_API_KEY": "",
  }
  process = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "src.main:app", "--port", str(port)],
    cwd=PROJECT_ROOT,
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
  )

  base = f"http://127.0.0.1:{port}"
  deadline = time.time() + 40
  import urllib.error
  import urllib.request

  while time.time() < deadline:
    if process.poll() is not None:
      raise RuntimeError(
        "uvicorn morreu:\n" + process.stdout.read().decode("utf-8", "replace")
      )
    try:
      with urllib.request.urlopen(f"{base}/health", timeout=1) as response:
        if response.status == 200:
          break
    except (urllib.error.URLError, OSError):
      time.sleep(0.25)
  else:
    process.kill()
    raise RuntimeError("o servidor nao respondeu em 40s")

  yield type("Server", (), {"base": base, "review": review, "db": db, "root": root})

  process.terminate()
  try:
    process.wait(timeout=10)
  except subprocess.TimeoutExpired:
    process.kill()


@pytest.fixture(scope="session")
def browser():
  from selenium import webdriver
  from selenium.webdriver.chrome.options import Options

  options = Options()
  options.add_argument("--headless=new")
  options.add_argument("--window-size=1680,1200")
  options.add_argument("--no-sandbox")
  driver = webdriver.Chrome(options=options)
  driver.set_window_size(1680, 1200)
  driver.set_script_timeout(30)
  yield driver
  driver.quit()


@pytest.fixture(autouse=True)
def fresh_state(live_server):
  """Reinicia banco e decisões antes de cada teste.

  O servidor é de sessão porque subir uvicorn custa ~2s e são dezenas de testes.
  O preço disso é que um teste que exclui ou renumera muda o corpus para os
  seguintes — foi o que quebrou quatro testes na primeira execução. Reinstalar o
  estado é mais barato que reiniciar o processo.
  """
  _seed(
    live_server.db,
    live_server.root / "prova.pdf",
    live_server.root / "media",
    schema=False,
  )
  for name in ("decisions.json", "audit.jsonl"):
    (live_server.review / name).unlink(missing_ok=True)
  yield


@pytest.fixture
def page(browser, live_server):
  """Abre /review já com um documento e a questão 1 selecionados."""
  browser.get(f"{live_server.base}/review")
  _wait(browser, "return document.querySelectorAll('.col .row').length > 0")
  browser.execute_script("document.querySelector('.col .row').click()")
  _wait(browser, "return document.querySelectorAll('.q').length > 0")
  browser.execute_script("document.querySelectorAll('.q')[0].click()")
  _wait(browser, "return !!document.querySelector('main h2')")
  return browser


def _wait(driver, script: str, timeout: float = 25.0):
  """Espera por uma condição em JS. Evita sleeps fixos, que ora sobram ora faltam."""
  deadline = time.time() + timeout
  while time.time() < deadline:
    if driver.execute_script(script):
      return True
    time.sleep(0.15)
  raise AssertionError(f"condicao nao satisfeita em {timeout}s: {script}")


@pytest.fixture
def wait():
  return _wait


@pytest.fixture
def console_errors(browser):
  """Falha o teste se o navegador logar erro — inclui 404 de recurso e exceção."""
  browser.get_log("browser")  # limpa o que veio antes
  yield
  noise = ("favicon", "Failed to load resource: net::ERR_")
  errors = [
    entry["message"]
    for entry in browser.get_log("browser")
    if entry["level"] == "SEVERE" and not any(n in entry["message"] for n in noise)
  ]
  assert not errors, "erros no console do navegador:\n" + "\n".join(errors)


@pytest.fixture
def operations(live_server):
  """Lê as operações registradas pelo servidor de teste."""

  def _read() -> list[dict]:
    path = live_server.review / "decisions.json"
    if not path.exists():
      return []
    return json.loads(path.read_text(encoding="utf-8")).get("operations", [])

  return _read
