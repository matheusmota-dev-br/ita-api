from pathlib import Path

from src.config import PROJECT_ROOT
from src.etl.transform.naming import ExamRef

# Subdiretórios dentro do diretório de uma prova processada.
QUESTIONS_SUBDIR = "questoes"
NON_QUESTIONS_SUBDIR = "fora_questoes"
TEXTS_SUBDIR = "textos"
DEBUG_SUBDIR = "debug"
# Saída do estágio de enriquecimento, um JSON por questão.
ENRICHED_SUBDIR = "enriquecido"

MANIFEST_FILENAME = "manifest.json"
ANSWER_KEY_FILENAME = "gabarito.json"
REGIONS_PDF_FILENAME = "questoes_regioes.pdf"
COVERAGE_REPORT_FILENAME = "coverage_report.json"


def get_processed_root(*, data_dir: Path | None = None) -> Path:
  """Raiz de todas as provas processadas: data/processed/."""
  return (data_dir or PROJECT_ROOT / "data") / "processed"


def get_exam_dir(ref: ExamRef, *, out_root: Path | None = None) -> Path:
  """Diretório de saída de um documento, namespaceado pelo slug.

  Sem esse namespace, processar a segunda prova sobrescreveria os PNGs da
  primeira — todas gerariam `questao_01.png` no mesmo lugar.
  """
  return (out_root or get_processed_root()) / ref.slug


def get_questions_figs_dir_for(ref: ExamRef, *, out_root: Path | None = None) -> Path:
  return get_exam_dir(ref, out_root=out_root) / QUESTIONS_SUBDIR


def get_non_questions_figs_dir_for(
  ref: ExamRef, *, out_root: Path | None = None
) -> Path:
  return get_exam_dir(ref, out_root=out_root) / NON_QUESTIONS_SUBDIR


def get_question_texts_dir_for(ref: ExamRef, *, out_root: Path | None = None) -> Path:
  return get_exam_dir(ref, out_root=out_root) / TEXTS_SUBDIR


def get_question_regions_pdf_path_for(
  ref: ExamRef, *, out_root: Path | None = None
) -> Path:
  return get_exam_dir(ref, out_root=out_root) / DEBUG_SUBDIR / REGIONS_PDF_FILENAME


def get_manifest_path(ref: ExamRef, *, out_root: Path | None = None) -> Path:
  return get_exam_dir(ref, out_root=out_root) / MANIFEST_FILENAME


# --- Getters legados -------------------------------------------------------
# Sem namespace por prova. Mantidos porque o notebook de análise
# (notebooks/01_file_analysis_for_extraction.ipynb) os utiliza. Código novo
# deve usar as variantes _for(ref) acima.


def get_base_data_dir(*, from_cwd: Path | None = None) -> Path:
  cwd = from_cwd or Path.cwd()
  if cwd.name == "notebooks":
    return (cwd.parent / "data" / "vestibular").resolve()
  return (cwd / "data" / "vestibular").resolve()


def get_questions_figs_dir(*, from_cwd: Path | None = None) -> Path:
  return get_base_data_dir(from_cwd=from_cwd) / "figs" / "questoes"


def get_non_questions_figs_dir(*, from_cwd: Path | None = None) -> Path:
  return get_base_data_dir(from_cwd=from_cwd) / "figs" / "fora_questoes"


def get_question_texts_dir(*, from_cwd: Path | None = None) -> Path:
  return get_base_data_dir(from_cwd=from_cwd) / "figs" / "textos_questoes"


def get_question_regions_pdf_path(*, from_cwd: Path | None = None) -> Path:
  return get_base_data_dir(from_cwd=from_cwd) / "figs" / "questoes_regioes.pdf"
