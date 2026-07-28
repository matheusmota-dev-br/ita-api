"""Orquestrador do ETL: extract -> transform -> load."""

import argparse
import sys

from src.etl.extract.main import main as extract_main
from src.etl.load.main import run_load
from src.etl.transform.batch import run_batch


def _run_all(args: argparse.Namespace) -> int:
  print("== extract ==")
  sys.argv = ["extract", args.target]
  if extract_main() != 0 and not args.keep_going:
    print("extract falhou; use --keep-going para seguir mesmo assim", file=sys.stderr)
    return 1

  print("\n== transform ==")
  report = run_batch(verbose=False)
  print(f"  {report.total_questions} questoes, status {report.by_status()}")

  print("\n== load ==")
  stats = run_load(verbose=False)
  print(f"  {stats.summary()}")
  return 0


def main() -> int:
  parser = argparse.ArgumentParser(description="Roda o pipeline completo.")
  parser.add_argument(
    "target",
    nargs="?",
    choices=["vestibular", "pos", "all"],
    default="all",
  )
  parser.add_argument(
    "--keep-going",
    action="store_true",
    help="segue para transform/load mesmo se algum download falhar",
  )
  return _run_all(parser.parse_args())


if __name__ == "__main__":
  sys.exit(main())
