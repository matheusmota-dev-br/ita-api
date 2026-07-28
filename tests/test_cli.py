"""Entry points de linha de comando.

O que se verifica aqui é o contrato de invocação: que `--help` funcione, que as
flags existam com o nome documentado, e que um comando termine com o código de
saída certo. Um `task load -- --rebuild` que morre por um argparse mal montado é
uma quebra que nenhum teste de unidade pega, porque a função interna funciona.
"""

import runpy
import subprocess
import sys

import pytest

from src.config import PROJECT_ROOT

MODULES = [
  "src.etl.extract",
  "src.etl.transform",
  "src.etl.load",
  "src.etl.enrich",
  "src.etl.verify",
]


@pytest.mark.parametrize("module", MODULES)
def test_help_works(module):
  """`--help` prova que o parser monta e que o módulo importa."""
  result = subprocess.run(
    [sys.executable, "-m", module, "--help"],
    cwd=PROJECT_ROOT,
    capture_output=True,
    text=True,
    timeout=90,
    env={"PYTHONPATH": str(PROJECT_ROOT), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
  )
  assert result.returncode == 0, result.stderr
  assert "usage" in result.stdout.lower()


@pytest.mark.parametrize(
  ("module", "flag"),
  [
    ("src.etl.load", "--rebuild"),
    ("src.etl.transform", "batch"),
    ("src.etl.transform", "report"),
    ("src.etl.enrich", "--dry-run"),
    ("src.etl.enrich", "--limit"),
    ("src.etl.verify", "--snapshot"),
  ],
)
def test_documented_flags_exist(module, flag):
  """As flags citadas no README e no REPRODUCIBILITY precisam existir."""
  result = subprocess.run(
    [sys.executable, "-m", module, "--help"],
    cwd=PROJECT_ROOT,
    capture_output=True,
    text=True,
    timeout=90,
    env={"PYTHONPATH": str(PROJECT_ROOT), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
  )
  assert flag in result.stdout, f"{module} nao aceita {flag}"


def test_unknown_flag_fails_loudly():
  """Errar o nome de uma flag tem que falhar, não ser ignorado em silêncio."""
  result = subprocess.run(
    [sys.executable, "-m", "src.etl.load", "--rebuidl"],
    cwd=PROJECT_ROOT,
    capture_output=True,
    text=True,
    timeout=90,
    env={"PYTHONPATH": str(PROJECT_ROOT), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
  )
  assert result.returncode != 0
  assert "unrecognized" in result.stderr.lower() or "error" in result.stderr.lower()


def test_orchestrator_module_imports():
  """`src/etl/main.py` já importou nomes inexistentes e quebrava com ImportError."""
  import src.etl.main as orchestrator

  assert hasattr(orchestrator, "main")


SUBCOMMAND_FLAGS = [
  ("batch", "--force"),
  ("batch", "--only"),
  ("single", "--force"),
]


@pytest.mark.parametrize(("sub", "flag"), SUBCOMMAND_FLAGS)
def test_transform_subcommand_flags(sub, flag):
  """O transform usa subcomandos: as flags ficam neles, não no topo.

  `task transform -- --force` funciona porque a task já injeta `batch` antes dos
  argumentos do usuário.
  """
  result = subprocess.run(
    [sys.executable, "-m", "src.etl.transform", sub, "--help"],
    cwd=PROJECT_ROOT,
    capture_output=True,
    text=True,
    timeout=90,
    env={"PYTHONPATH": str(PROJECT_ROOT), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
  )
  assert result.returncode == 0, result.stderr
  assert flag in result.stdout


def test_transform_report_runs_without_a_corpus(monkeypatch, capsys):
  """`task coverage` chama `report`; sem corpus processado ele relata o vazio."""
  from src.etl.transform.main import main

  monkeypatch.setattr("sys.argv", ["transform", "report"])
  assert main() in (0, 1)
  capsys.readouterr()


def test_main_module_is_runnable_as_a_script():
  """`python -m src.etl.load` passa por `__main__.py`, que é código próprio."""
  with pytest.raises(SystemExit) as exit_info:
    sys.argv = ["load", "--help"]
    runpy.run_module("src.etl.load", run_name="__main__")
  assert exit_info.value.code == 0
