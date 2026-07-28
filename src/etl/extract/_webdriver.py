from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from src.config import PROJECT_ROOT

LOCAL_CHROMEDRIVER = PROJECT_ROOT / "chromedriver"


def build_chrome(*, headless: bool = True) -> webdriver.Chrome:
  """Sobe o Chrome usando o chromedriver da raiz do projeto, se existir.

  `src/download_chromedriver.py` grava o binário em PROJECT_ROOT/chromedriver,
  mas nada o usava — o Selenium caía no Selenium Manager. Sem o binário local,
  o Selenium Manager continua sendo o fallback.
  """
  options = Options()
  if headless:
    options.add_argument("--headless=new")
  options.add_argument("--disable-gpu")
  options.add_argument("--no-sandbox")
  options.add_argument("--window-size=1400,1200")

  service = _local_service()
  return (
    webdriver.Chrome(options=options, service=service)
    if service
    else webdriver.Chrome(options=options)
  )


def _local_service() -> Service | None:
  path: Path = LOCAL_CHROMEDRIVER
  if path.is_file():
    return Service(executable_path=str(path))
  return None
