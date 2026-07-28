from src.etl.transform.batch import (
  CoverageReport,
  DocumentResult,
  run_batch,
  transform_document,
)
from src.etl.transform.constants import (
  DPI,
  MARGIN_BOTTOM,
  MARGIN_LEFT,
  MARGIN_TOP,
  MAX_QUESTIONS,
  OBJECTIVE_OPTION_LETTERS,
  QUESTION_END_REGEX,
  QUESTION_IN_TEXT_REGEX,
  QUESTION_NUMBER_ONLY_LINE_REGEX,
  QUESTION_START_REGEX,
  QUESTION_WORD_ALONE_REGEX,
)
from src.etl.transform.export_text import save_question_text_files
from src.etl.transform.images import (
  export_non_question_figures,
  export_question_figures,
  export_question_regions_pdf,
)
from src.etl.transform.intervals import (
  merge_intervals,
  non_question_intervals_on_page,
  question_intervals_on_page,
)
from src.etl.transform.labels import Candidate, LabelStyle, detect_question_positions
from src.etl.transform.main import run_transform
from src.etl.transform.manifest import ExamManifest, QuestionManifest
from src.etl.transform.naming import ExamRef, iter_exam_refs, parse_pdf_path
from src.etl.transform.pages import is_draft_page, is_essay_page, page_has_question
from src.etl.transform.paths import (
  get_base_data_dir,
  get_exam_dir,
  get_manifest_path,
  get_non_questions_figs_dir,
  get_non_questions_figs_dir_for,
  get_processed_root,
  get_question_regions_pdf_path,
  get_question_regions_pdf_path_for,
  get_question_texts_dir,
  get_question_texts_dir_for,
  get_questions_figs_dir,
  get_questions_figs_dir_for,
)
from src.etl.transform.positions import find_question_positions
from src.etl.transform.rectangles import build_question_rectangles
from src.etl.transform.sections import assign_disciplines, find_section_headers
from src.etl.transform.text_extraction import (
  block_contains_question,
  extract_question_texts,
  get_text_in_rect,
)
from src.etl.transform.text_utils import normalize_key, strip_accents

# A função `main` não é reexportada de propósito: o nome sombrearia o submódulo
# de mesmo nome, e `import src.etl.transform.main` passaria a devolver a
# função em vez do módulo. Quem precisa dela importa do módulo direto, como o
# __main__.py já faz.
__all__ = [
  "assign_disciplines",
  "block_contains_question",
  "build_question_rectangles",
  "Candidate",
  "CoverageReport",
  "detect_question_positions",
  "DocumentResult",
  "DPI",
  "ExamManifest",
  "ExamRef",
  "export_non_question_figures",
  "export_question_figures",
  "export_question_regions_pdf",
  "extract_question_texts",
  "find_question_positions",
  "find_section_headers",
  "get_base_data_dir",
  "get_exam_dir",
  "get_manifest_path",
  "get_non_questions_figs_dir",
  "get_non_questions_figs_dir_for",
  "get_processed_root",
  "get_question_regions_pdf_path",
  "get_question_regions_pdf_path_for",
  "get_question_texts_dir",
  "get_question_texts_dir_for",
  "get_questions_figs_dir",
  "get_questions_figs_dir_for",
  "get_text_in_rect",
  "is_draft_page",
  "is_essay_page",
  "iter_exam_refs",
  "LabelStyle",
  "MARGIN_BOTTOM",
  "MARGIN_LEFT",
  "MARGIN_TOP",
  "MAX_QUESTIONS",
  "merge_intervals",
  "non_question_intervals_on_page",
  "normalize_key",
  "OBJECTIVE_OPTION_LETTERS",
  "page_has_question",
  "parse_pdf_path",
  "question_intervals_on_page",
  "QUESTION_END_REGEX",
  "QUESTION_IN_TEXT_REGEX",
  "QUESTION_NUMBER_ONLY_LINE_REGEX",
  "QUESTION_START_REGEX",
  "QUESTION_WORD_ALONE_REGEX",
  "QuestionManifest",
  "run_batch",
  "run_transform",
  "save_question_text_files",
  "strip_accents",
  "transform_document",
]
