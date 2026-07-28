import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Sidecar gravado ao lado dos PDFs. É o que preserva de qual das três eras do
# site cada arquivo veio: a coluna "fisica" significa prova de fase única em
# 2008–2018 e prova de 2ª fase de 2019 em diante, e o nome do PDF sozinho não
# distingue os dois casos.
INDEX_FILENAME = "_index.json"
REPORT_FILENAME = "download_report.json"

REQUEST_TIMEOUT = 60
RETRY_TOTAL = 4
RETRY_BACKOFF = 1.5


@dataclass
class DownloadEntry:
  filename: str
  format_index: int
  year: str
  column: str
  url: str
  sha256: str
  byte_size: int


@dataclass
class DownloadReport:
  downloaded: list[str] = field(default_factory=list)
  skipped: list[str] = field(default_factory=list)
  failed: list[dict] = field(default_factory=list)

  @property
  def ok(self) -> bool:
    return not self.failed

  def summary(self) -> str:
    return (
      f"{len(self.downloaded)} baixados, "
      f"{len(self.skipped)} já existentes, "
      f"{len(self.failed)} falhas"
    )


def _build_session() -> requests.Session:
  session = requests.Session()
  retry = Retry(
    total=RETRY_TOTAL,
    backoff_factor=RETRY_BACKOFF,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET"]),
  )
  adapter = HTTPAdapter(max_retries=retry)
  session.mount("http://", adapter)
  session.mount("https://", adapter)
  return session


def load_index(provas_dir: Path) -> dict[str, dict]:
  """Lê o sidecar de índice; devolve {} se ainda não existir."""
  index_path = provas_dir / INDEX_FILENAME
  if not index_path.exists():
    return {}
  return json.loads(index_path.read_text(encoding="utf-8"))


def _write_index(provas_dir: Path, index: dict[str, dict]) -> None:
  path = provas_dir / INDEX_FILENAME
  path.write_text(
    json.dumps(index, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
  )


def download_pdfs_from_jsons(
  *,
  output_dir: Path,
  n: int,
  json_prefix: str,
  pdf_filename_prefix: str = "prova_ita_vestibular",
  force: bool = False,
) -> DownloadReport:
  """Baixa todos os PDFs referenciados pelos JSONs raspados do site.

  Falhas não são engolidas: entram no relatório e em download_report.json, para
  que um corpus parcial não fique indistinguível de um completo.
  """
  provas_dir = output_dir / "provas"
  provas_dir.mkdir(parents=True, exist_ok=True)

  index = load_index(provas_dir)
  report = DownloadReport()
  session = _build_session()

  try:
    for i in range(n):
      # i=0 é a primeira tabela da página (era mais recente) e recebe o maior
      # número; 1a_ é a era mais antiga. format_index preserva essa ordem.
      format_index = n - i
      filepath = output_dir / f"{format_index}{json_prefix}"
      if not filepath.exists():
        continue

      rows = json.loads(filepath.read_text(encoding="utf-8"))
      for row in rows:
        items = list(row.items())
        if not items:
          continue
        year = items[0][1]
        for column, value in items[1:]:
          if not isinstance(value, str) or not value.startswith("http"):
            continue

          filename = f"{pdf_filename_prefix}_{year}_{column}.pdf"
          target = provas_dir / filename

          if target.exists() and filename in index and not force:
            report.skipped.append(filename)
            continue

          try:
            response = session.get(value, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
          except requests.RequestException as exc:
            report.failed.append(
              {"filename": filename, "url": value, "error": str(exc)}
            )
            continue

          content = response.content
          target.write_bytes(content)
          index[filename] = asdict(
            DownloadEntry(
              filename=filename,
              format_index=format_index,
              year=str(year),
              column=column,
              url=value,
              sha256=hashlib.sha256(content).hexdigest(),
              byte_size=len(content),
            )
          )
          report.downloaded.append(filename)
  finally:
    session.close()
    _write_index(provas_dir, index)
    (output_dir / REPORT_FILENAME).write_text(
      json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8"
    )

  return report
