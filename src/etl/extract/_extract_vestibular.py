import json
from pathlib import Path

from src.etl.extract._download_pdfs_from_jsons import (
  DownloadReport,
  download_pdfs_from_jsons,
)
from src.etl.extract._driver_provas_vestibular_ita import DRIVER_PROVAS_VESTIBULAR_ITA
from src.etl.extract._json_file_exist import json_files_exist
from src.etl.extract._webdriver import build_chrome

OUTPUT_DIR = Path("data/vestibular")
JSON_PREFIX = "a_formato_prova_ita_vestibular.json"


def _create_json_files(output_dir: Path, headless: bool) -> None:
  tables = DRIVER_PROVAS_VESTIBULAR_ITA.tables
  n = len(tables)

  driver = build_chrome(headless=headless)
  try:
    driver.get(DRIVER_PROVAS_VESTIBULAR_ITA.url)
    DRIVER_PROVAS_VESTIBULAR_ITA.navigate(driver)
    for i, table in enumerate(tables):
      data = table.get_data(driver)
      filepath = output_dir / f"{n - i}{JSON_PREFIX}"
      filepath.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
      )
  finally:
    driver.quit()


def extract_vestibular(
  *,
  headless: bool = True,
  force_replace: bool = False,
  force_download: bool = False,
  output_dir: Path | None = None,
) -> DownloadReport:
  output_dir = output_dir or OUTPUT_DIR
  output_dir.mkdir(parents=True, exist_ok=True)

  tables = DRIVER_PROVAS_VESTIBULAR_ITA.tables
  n = len(tables)
  json_exist = json_files_exist(
    output_dir=output_dir,
    n=n,
    json_prefix=JSON_PREFIX,
  )

  if force_replace or not json_exist:
    _create_json_files(output_dir, headless)

  return download_pdfs_from_jsons(
    output_dir=output_dir,
    n=n,
    json_prefix=JSON_PREFIX,
    force=force_download,
  )
