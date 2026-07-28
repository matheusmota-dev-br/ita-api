"""Processa o corpus inteiro de PDFs, um diretório de saída por documento."""

import json
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from src.constants import Discipline, FormatType
from src.etl.provenance import write as write_provenance
from src.etl.transform.answer_keys import parse_answer_key
from src.etl.transform.images import (
  export_non_question_figures,
  export_question_figures,
  export_question_regions_pdf,
)
from src.etl.transform.labels import detect_question_positions
from src.etl.transform.manifest import (
  SCHEMA_VERSION,
  TRANSFORM_VERSION,
  DocumentManifest,
  ExamManifest,
  PartManifest,
  QuestionManifest,
  RegionManifest,
  question_source_sha256,
  read_manifest,
  sha256_bytes,
  sha256_file,
)
from src.etl.transform.naming import ExamRef, iter_exam_refs
from src.etl.transform.ocr import document_needs_ocr, install_hint, is_available
from src.etl.transform.paths import (
  ANSWER_KEY_FILENAME,
  COVERAGE_REPORT_FILENAME,
  get_exam_dir,
  get_manifest_path,
  get_non_questions_figs_dir_for,
  get_processed_root,
  get_question_regions_pdf_path_for,
  get_question_texts_dir_for,
  get_questions_figs_dir_for,
)
from src.etl.transform.rectangles import build_question_rectangles
from src.etl.transform.sections import assign_disciplines
from src.etl.transform.text_extraction import extract_question_texts
from src.models.enums import DisciplineSource, DocumentKind

# Teto generoso: a 1ª fase variou de 48 (2025) a 70 (2021) questões, então um
# limite fixo em 60 truncaria anos inteiros em silêncio.
MAX_QUESTIONS_CEILING = 300

PROVAS_DIRS = (Path("data/vestibular/provas"), Path("data/pos/provas"))


@dataclass
class DocumentResult:
  slug: str
  status: str
  questions: int
  pages: int
  label_style: str | None = None
  warnings: list[str] = field(default_factory=list)
  error: str | None = None


@dataclass
class CoverageReport:
  transform_version: str = TRANSFORM_VERSION
  documents: list[DocumentResult] = field(default_factory=list)

  @property
  def total_questions(self) -> int:
    return sum(d.questions for d in self.documents)

  def by_status(self) -> dict[str, int]:
    counts: dict[str, int] = {}
    for doc in self.documents:
      counts[doc.status] = counts.get(doc.status, 0) + 1
    return counts


def _page_text_lengths(doc: pymupdf.Document) -> list[int]:
  return [len(doc[i].get_text().strip()) for i in range(len(doc))]


def _detect_warnings(doc: pymupdf.Document, numbers: list[int]) -> list[str]:
  warnings: list[str] = []

  lengths = _page_text_lengths(doc)
  blank = [i for i, n in enumerate(lengths) if n == 0]
  if blank:
    warnings.append(f"paginas sem camada de texto (provavel digitalizacao): {blank}")

  if not numbers:
    warnings.append("nenhuma questao detectada")
    return warnings

  missing = [n for n in range(numbers[0], numbers[-1] + 1) if n not in set(numbers)]
  if missing:
    warnings.append(f"numeros ausentes dentro da faixa detectada: {missing}")
  if numbers[0] != 1:
    # Legítimo em algumas provas: Português de 2008–2018 começa em 20, dando
    # sequência ao Inglês. Fica registrado para conferência, não é erro.
    warnings.append(f"numeracao comeca em {numbers[0]}, nao em 1")
  return warnings


def _relative(path: Path, root: Path) -> str:
  return str(path.relative_to(root)).replace("\\", "/")


def _build_question_manifests(
  doc: pymupdf.Document,
  ref: ExamRef,
  question_rects: dict[int, list[tuple[int, pymupdf.Rect]]],
  positions: list[tuple[int, int, pymupdf.Rect]],
  exam_dir: Path,
  figs_dir: Path,
  texts_dir: Path,
  use_ocr: bool = False,
) -> list[QuestionManifest]:
  inside, outside = extract_question_texts(doc, question_rects, use_ocr=use_ocr)
  disciplines = assign_disciplines(doc, positions) if ref.discipline is None else {}

  manifests: list[QuestionManifest] = []
  for number in sorted(question_rects):
    clips = question_rects[number]
    parts: list[PartManifest] = []
    image_hashes: list[str] = []

    for part_index, (page_no, clip) in enumerate(clips):
      suffix = "" if len(clips) == 1 else f"_p{part_index + 1}"
      image_path = figs_dir / f"questao_{number:02d}{suffix}.png"
      image_sha = sha256_file(image_path) if image_path.exists() else None
      if image_sha:
        image_hashes.append(image_sha)
      parts.append(
        PartManifest(
          part_index=part_index,
          page_no=page_no,
          bbox=[clip.x0, clip.y0, clip.x1, clip.y1],
          image=_relative(image_path, exam_dir) if image_path.exists() else None,
          image_sha256=image_sha,
        )
      )

    statement = inside.get(number, "")
    text_inside = texts_dir / f"questao_{number:02d}_dentro.txt"
    text_outside = texts_dir / f"questao_{number:02d}_fora.txt"

    if ref.discipline is not None:
      discipline: Discipline | None = ref.discipline
      source: DisciplineSource | None = DisciplineSource.FILENAME
      confidence: float | None = 1.0
    elif number in disciplines:
      discipline, confidence = disciplines[number]
      source = DisciplineSource.SECTION_HEADER
    else:
      discipline, source, confidence = None, None, None

    manifests.append(
      QuestionManifest(
        number=number,
        format_type=ref.default_format_type.value,
        page_start=clips[0][0],
        page_end=clips[-1][0],
        parts=parts,
        text_inside=_relative(text_inside, exam_dir) if text_inside.exists() else None,
        text_outside=(
          _relative(text_outside, exam_dir) if text_outside.exists() else None
        ),
        discipline=discipline.value if discipline else None,
        discipline_source=source.value if source else None,
        discipline_confidence=confidence,
        source_sha256=question_source_sha256(statement, image_hashes),
      )
    )
  return manifests


def _collect_regions(non_questions_dir: Path, exam_dir: Path) -> list[RegionManifest]:
  regions: list[RegionManifest] = []
  if not non_questions_dir.is_dir():
    return regions
  for image in sorted(non_questions_dir.glob("pagina_*.png")):
    stem = image.stem
    _, page_part, *label_parts = stem.split("_")
    regions.append(
      RegionManifest(
        page_no=int(page_part),
        label="_".join(label_parts) or "desconhecido",
        image=_relative(image, exam_dir),
        sha256=sha256_bytes(image.read_bytes()),
      )
    )
  return regions


def transform_answer_key(
  ref: ExamRef,
  *,
  out_root: Path | None = None,
  force: bool = False,
) -> DocumentResult:
  """Parseia um PDF de gabarito e grava gabarito.json ao lado do manifest."""
  exam_dir = get_exam_dir(ref, out_root=out_root)
  target = exam_dir / ANSWER_KEY_FILENAME
  pdf_sha = sha256_file(ref.path)

  if not force and target.exists():
    existing = json.loads(target.read_text(encoding="utf-8"))
    if existing.get("document", {}).get("sha256") == pdf_sha:
      return DocumentResult(
        ref.slug, "cached", len(existing.get("answers", [])), 0, label_style="gabarito"
      )

  try:
    doc = pymupdf.open(ref.path)
  except (pymupdf.FileDataError, RuntimeError, OSError) as exc:
    return DocumentResult(ref.slug, "error", 0, 0, error=str(exc))

  try:
    answers = parse_answer_key(doc)
    pages = len(doc)
  except (pymupdf.mupdf.FzErrorBase, RuntimeError, ValueError) as exc:
    doc.close()
    return DocumentResult(ref.slug, "error", 0, 0, error=f"{type(exc).__name__}: {exc}")
  doc.close()

  unlabelled = sum(1 for a in answers if a.discipline is None)
  warnings = []
  if not answers:
    warnings.append("nenhuma resposta reconhecida")
  if unlabelled:
    warnings.append(f"{unlabelled} respostas sem materia identificada")

  exam_dir.mkdir(parents=True, exist_ok=True)
  target.write_text(
    json.dumps(
      {
        "schema_version": SCHEMA_VERSION,
        "transform_version": TRANSFORM_VERSION,
        "slug": ref.slug,
        "exam_slug": ref.exam_slug,
        "test_type": ref.test_type.value,
        "year": ref.year,
        "phase": ref.phase.value,
        "source_column": ref.source_column,
        "document": {
          "local_path": str(ref.path),
          "sha256": pdf_sha,
          "page_count": pages,
        },
        "answers": [
          {
            "number": a.number,
            "letter": a.letter,
            "annulled": a.annulled,
            "discipline": a.discipline.value if a.discipline else None,
            "column": a.column,
            "raw": a.raw,
          }
          for a in answers
        ],
        "warnings": warnings,
      },
      indent=2,
      ensure_ascii=False,
    ),
    encoding="utf-8",
  )

  return DocumentResult(
    slug=ref.slug,
    status="ok" if answers else "empty",
    questions=len(answers),
    pages=pages,
    label_style="gabarito",
    warnings=warnings,
  )


def transform_document(
  ref: ExamRef,
  *,
  out_root: Path | None = None,
  force: bool = False,
) -> DocumentResult:
  """Processa um PDF e grava seu manifest. Nunca levanta por PDF inválido."""
  exam_dir = get_exam_dir(ref, out_root=out_root)
  manifest_path = get_manifest_path(ref, out_root=out_root)

  pdf_sha = sha256_file(ref.path)
  existing = read_manifest(manifest_path)
  if (
    not force
    and existing is not None
    and existing.get("document", {}).get("sha256") == pdf_sha
    and existing.get("transform_version") == TRANSFORM_VERSION
  ):
    return DocumentResult(
      slug=ref.slug,
      status="cached",
      questions=len(existing.get("questions", [])),
      pages=existing.get("document", {}).get("page_count", 0),
      label_style=existing.get("label_style"),
      warnings=existing.get("warnings", []),
    )

  try:
    doc = pymupdf.open(ref.path)
  except (pymupdf.FileDataError, RuntimeError, OSError) as exc:
    return DocumentResult(ref.slug, "error", 0, 0, error=str(exc))

  try:
    return _transform_open_document(
      ref, doc, pdf_sha, exam_dir, manifest_path, out_root
    )
  # Um PDF malformado não pode derrubar o lote inteiro: o documento vira
  # status="error" no relatório de cobertura e o processamento continua.
  except (pymupdf.mupdf.FzErrorBase, RuntimeError, OSError, ValueError) as exc:
    return DocumentResult(
      ref.slug, "error", 0, len(doc), error=f"{type(exc).__name__}: {exc}"
    )
  finally:
    doc.close()


def _transform_open_document(
  ref: ExamRef,
  doc: pymupdf.Document,
  pdf_sha: str,
  exam_dir: Path,
  manifest_path: Path,
  out_root: Path | None,
) -> DocumentResult:
  is_essay = ref.default_format_type is FormatType.ESSAY
  positions, style = (
    ([], None)
    if is_essay
    else detect_question_positions(doc, max_questions=MAX_QUESTIONS_CEILING)
  )

  # Segunda tentativa com OCR: os PDFs digitalizados não têm camada de texto,
  # então a primeira passada não tinha em que trabalhar. Só roda quando a
  # primeira falhou e o Tesseract está disponível — é lento (300 DPI por
  # página) e desnecessário para os 141 documentos que já funcionam.
  used_ocr = False
  if not positions and not is_essay and document_needs_ocr(doc) and is_available():
    positions, style = detect_question_positions(
      doc, max_questions=MAX_QUESTIONS_CEILING, use_ocr=True
    )
    used_ocr = bool(positions)
  numbers = sorted(n for n, _, _ in positions)
  question_rects = build_question_rectangles(doc, positions) if positions else {}

  figs_dir = get_questions_figs_dir_for(ref, out_root=out_root)
  non_questions_dir = get_non_questions_figs_dir_for(ref, out_root=out_root)
  texts_dir = get_question_texts_dir_for(ref, out_root=out_root)

  if question_rects:
    export_question_figures(doc, question_rects, out_dir=figs_dir)
    export_non_question_figures(doc, question_rects, out_dir=non_questions_dir)
    export_question_regions_pdf(
      doc,
      question_rects,
      output_path=get_question_regions_pdf_path_for(ref, out_root=out_root),
    )
    inside, outside = extract_question_texts(doc, question_rects, use_ocr=used_ocr)
    _write_texts(texts_dir, inside, outside)

  questions = (
    _build_question_manifests(
      doc, ref, question_rects, positions, exam_dir, figs_dir, texts_dir, used_ocr
    )
    if question_rects
    else []
  )

  warnings = [] if is_essay else _detect_warnings(doc, numbers)
  if used_ocr:
    warnings.append("questoes localizadas por OCR; o texto pode conter erros")
  elif not positions and not is_essay and document_needs_ocr(doc):
    warnings.append(install_hint())
  if is_essay:
    warnings.append("documento de redacao: sem questoes numeradas por definicao")

  manifest = ExamManifest(
    schema_version=SCHEMA_VERSION,
    transform_version=TRANSFORM_VERSION,
    slug=ref.slug,
    exam_slug=ref.exam_slug,
    test_type=ref.test_type.value,
    year=ref.year,
    phase=ref.phase.value,
    discipline=ref.discipline.value if ref.discipline else None,
    kind=ref.kind.value,
    source_column=ref.source_column,
    format_index=ref.format_index,
    label_style=style.value if style else None,
    document=DocumentManifest(
      local_path=str(ref.path),
      sha256=pdf_sha,
      page_count=len(doc),
      source_url=None,
    ),
    questions=questions,
    regions=_collect_regions(non_questions_dir, exam_dir),
    warnings=warnings,
  )
  manifest.write(manifest_path)

  status = "ok" if questions else ("essay" if is_essay else "empty")
  return DocumentResult(
    slug=ref.slug,
    status=status,
    questions=len(questions),
    pages=len(doc),
    label_style=style.value if style else None,
    warnings=warnings,
  )


def _write_texts(
  texts_dir: Path, inside: dict[int, str], outside: dict[int, str]
) -> None:
  texts_dir.mkdir(parents=True, exist_ok=True)
  for number, text in inside.items():
    (texts_dir / f"questao_{number:02d}_dentro.txt").write_text(text, encoding="utf-8")
    if outside.get(number):
      (texts_dir / f"questao_{number:02d}_fora.txt").write_text(
        outside[number], encoding="utf-8"
      )


def run_batch(
  *,
  provas_dirs: tuple[Path, ...] = PROVAS_DIRS,
  out_root: Path | None = None,
  only_slug: str | None = None,
  force: bool = False,
  verbose: bool = True,
) -> CoverageReport:
  out_root = out_root or get_processed_root()
  report = CoverageReport()

  refs: list[ExamRef] = []
  for provas_dir in provas_dirs:
    if provas_dir.is_dir():
      refs.extend(iter_exam_refs(provas_dir))

  for ref in refs:
    if only_slug and only_slug not in ref.slug:
      continue

    if ref.kind is DocumentKind.GABARITO:
      result = transform_answer_key(ref, out_root=out_root, force=force)
    else:
      result = transform_document(ref, out_root=out_root, force=force)
    report.documents.append(result)
    if verbose:
      flag = (
        "" if result.status in ("ok", "cached", "essay") else f"  <- {result.status}"
      )
      print(f"  {result.slug:44} {result.questions:4} questoes{flag}")

  out_root.mkdir(parents=True, exist_ok=True)
  # Versões de PyMuPDF, Tesseract e do código, ao lado do resultado que elas
  # produziram — é o que permite atribuir uma divergência futura a uma causa.
  write_provenance()
  (out_root / COVERAGE_REPORT_FILENAME).write_text(
    json.dumps(
      {
        "transform_version": report.transform_version,
        "total_questions": report.total_questions,
        "by_status": report.by_status(),
        "documents": [vars(d) for d in report.documents],
      },
      indent=2,
      ensure_ascii=False,
    ),
    encoding="utf-8",
  )
  return report
