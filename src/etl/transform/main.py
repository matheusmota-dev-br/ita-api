import argparse
import json
import sys
from pathlib import Path

import pymupdf

from src.etl.transform.batch import MAX_QUESTIONS_CEILING, run_batch, transform_document
from src.etl.transform.constants import MAX_QUESTIONS
from src.etl.transform.export_text import save_question_text_files
from src.etl.transform.images import (
  export_non_question_figures,
  export_question_figures,
  export_question_regions_pdf,
)
from src.etl.transform.labels import detect_question_positions
from src.etl.transform.naming import parse_pdf_path
from src.etl.transform.paths import (
  COVERAGE_REPORT_FILENAME,
  get_non_questions_figs_dir_for,
  get_processed_root,
  get_question_regions_pdf_path_for,
  get_question_texts_dir_for,
  get_questions_figs_dir_for,
)
from src.etl.transform.positions import find_question_positions
from src.etl.transform.rectangles import build_question_rectangles
from src.etl.transform.text_extraction import extract_question_texts


def run_transform(
  doc: pymupdf.Document,
  *,
  exam_ref=None,
  out_root: Path | None = None,
  max_questions: int = MAX_QUESTIONS,
  export_question_images: bool = True,
  export_non_question_images: bool = True,
  export_texts: bool = True,
  export_regions_pdf: bool = True,
  questions_figs_dir: Path | None = None,
  non_questions_figs_dir: Path | None = None,
  texts_dir: Path | None = None,
  regions_pdf_path: Path | None = None,
) -> dict:
  """Extrai questões de um documento já aberto.

  Ordem de resolução dos diretórios: caminho explícito, depois o diretório
  namespaceado por `exam_ref`, e só então o getter legado sem namespace.
  """
  if exam_ref is not None:
    questions_figs_dir = questions_figs_dir or get_questions_figs_dir_for(
      exam_ref, out_root=out_root
    )
    non_questions_figs_dir = non_questions_figs_dir or get_non_questions_figs_dir_for(
      exam_ref, out_root=out_root
    )
    texts_dir = texts_dir or get_question_texts_dir_for(exam_ref, out_root=out_root)
    regions_pdf_path = regions_pdf_path or get_question_regions_pdf_path_for(
      exam_ref, out_root=out_root
    )

  positions = find_question_positions(doc, max_questions=max_questions)
  question_rects = build_question_rectangles(doc, positions)
  result = {"positions": positions, "question_rects": question_rects}

  if export_question_images:
    result["questions_figs_dir"] = export_question_figures(
      doc, question_rects, out_dir=questions_figs_dir
    )
  if export_non_question_images:
    result["non_questions_figs_dir"] = export_non_question_figures(
      doc, question_rects, out_dir=non_questions_figs_dir
    )
  if export_texts:
    inside, outside = extract_question_texts(doc, question_rects)
    result["inside_texts"] = inside
    result["outside_texts"] = outside
    result["texts_dir"] = save_question_text_files(inside, outside, out_dir=texts_dir)
  if export_regions_pdf:
    result["regions_pdf_path"] = export_question_regions_pdf(
      doc, question_rects, output_path=regions_pdf_path
    )
  return result


def _cmd_single(args: argparse.Namespace) -> int:
  path = Path(args.pdf)
  ref = parse_pdf_path(path)
  if ref is None:
    print(f"nome de arquivo nao reconhecido: {path.name}", file=sys.stderr)
    return 1

  doc = pymupdf.open(path)
  try:
    positions, style = detect_question_positions(
      doc, max_questions=MAX_QUESTIONS_CEILING
    )
    numbers = sorted(n for n, _, _ in positions)
    print(f"{ref.slug}: {len(numbers)} questoes, estilo={style}")
    if numbers:
      print(f"  faixa {numbers[0]}..{numbers[-1]}")
  finally:
    doc.close()

  result = transform_document(ref, force=args.force)
  print(f"  status={result.status} paginas={result.pages}")
  for warning in result.warnings:
    print(f"  aviso: {warning}")
  return 0


def _cmd_batch(args: argparse.Namespace) -> int:
  report = run_batch(
    only_slug=args.only,
    force=args.force,
  )
  print(f"\ntotal de questoes: {report.total_questions}")
  print(f"por status: {report.by_status()}")
  return 0


def _cmd_report(_args: argparse.Namespace) -> int:
  path = get_processed_root() / COVERAGE_REPORT_FILENAME
  if not path.exists():
    print("nenhum relatorio encontrado; rode `task transform` antes", file=sys.stderr)
    return 1

  report = json.loads(path.read_text(encoding="utf-8"))
  docs = report["documents"]
  print(f"questoes: {report['total_questions']}   documentos: {len(docs)}")
  print(f"status: {report['by_status']}")

  needs_llm = [d for d in docs if d["status"] == "empty"]
  if needs_llm:
    print(f"\n{len(needs_llm)} documentos sem deteccao heuristica:")
    for doc in needs_llm:
      reason = next(
        (w for w in doc["warnings"] if "sem camada" in w),
        "texto presente, rotulo nao reconhecido",
      )
      print(f"  {doc['slug']:38} {doc['pages']:3}pg  {reason[:52]}")

  flagged = [
    d for d in docs if any("ausentes" in w for w in d["warnings"]) and d["questions"]
  ]
  if flagged:
    print(f"\n{len(flagged)} documentos com numeros faltando na faixa detectada:")
    for doc in flagged:
      gap = next(w for w in doc["warnings"] if "ausentes" in w)
      print(f"  {doc['slug']:38} {gap[:56]}")
  return 0


def main() -> int:
  parser = argparse.ArgumentParser(description="Recorta questões dos PDFs baixados.")
  sub = parser.add_subparsers(dest="command")

  single = sub.add_parser("single", help="processa um PDF")
  single.add_argument("pdf")
  single.add_argument("--force", action="store_true")
  single.set_defaults(func=_cmd_single)

  batch = sub.add_parser("batch", help="processa o corpus inteiro")
  batch.add_argument("--only", default=None, help="filtra por trecho do slug")
  batch.add_argument("--force", action="store_true")
  batch.set_defaults(func=_cmd_batch)

  report = sub.add_parser("report", help="resume o relatorio de cobertura")
  report.set_defaults(func=_cmd_report)

  args = parser.parse_args()
  if not hasattr(args, "func"):
    args = parser.parse_args(["batch"])
  return args.func(args)


if __name__ == "__main__":
  sys.exit(main())
