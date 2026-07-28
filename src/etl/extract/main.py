import argparse
import sys

from src.etl.extract._extract_pos import extract_pos
from src.etl.extract._extract_vestibular import extract_vestibular

EXTRACTORS = {"vestibular": extract_vestibular, "pos": extract_pos}


def main() -> int:
  parser = argparse.ArgumentParser(description="Baixa as provas do site do ITA.")
  parser.add_argument(
    "type",
    nargs="?",
    choices=[*EXTRACTORS, "all"],
    default=None,
    help="o que extrair: vestibular, pos ou all",
  )
  parser.add_argument(
    "--no-headless",
    action="store_true",
    help="roda o Chrome com a janela visível",
  )
  parser.add_argument(
    "--force",
    action="store_true",
    default=False,
    help="regera os JSONs de índice a partir do site mesmo se já existirem",
  )
  parser.add_argument(
    "--force-download",
    action="store_true",
    default=False,
    help="rebaixa os PDFs, inclusive os que já estão em disco",
  )
  args = parser.parse_args()

  if args.type is None:
    parser.error("é obrigatório escolher o que extrair: vestibular, pos ou all")

  targets = list(EXTRACTORS) if args.type == "all" else [args.type]
  failed = 0

  for target in targets:
    print(f"== {target} ==")
    report = EXTRACTORS[target](
      headless=not args.no_headless,
      force_replace=args.force,
      force_download=args.force_download,
    )
    print(f"   {report.summary()}")
    for failure in report.failed:
      print(f"   FALHA {failure['filename']}: {failure['error']}")
    failed += len(report.failed)

  # Exit code não-zero quando algo falhou: um corpus parcial não pode passar
  # despercebido dentro de um pipeline.
  return 1 if failed else 0


if __name__ == "__main__":
  sys.exit(main())
