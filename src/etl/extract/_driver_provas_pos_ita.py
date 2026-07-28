import re
from dataclasses import dataclass

from selenium.webdriver.common.by import By

from src.etl.extract.constants import (
  PROVAS_ITA_POS_URL,
)
from src.utils.selenium_elements import (
  BaseElement,
  ITAProvasFlow,
  SeleniumElement,
  SeleniumTable,
  SeleniumTableRow,
)


def _filename_starts_with_prova(href: str) -> bool:
  if not href:
    return False
  name = href.rstrip("/").split("/")[-1].split("?")[0]
  return name.startswith("Prova")


def _parse_prova_href(href: str) -> tuple[str, str] | None:
  if not href or not _filename_starts_with_prova(href):
    return None
  name = href.rstrip("/").split("/")[-1].split("?")[0]
  sem = (
    "1o_semestre"
    if "1oSem" in name or "-1o" in name
    else ("2o_semestre" if "2oSem" in name or "-2o" in name else None)
  )
  if not sem:
    return None
  tipo = "ingles" if "Ingles" in name else "matematica"
  return (sem, tipo)


@dataclass
class ProvasPosTable(SeleniumTable):
  def get_data(self, driver) -> list[dict[str, str]]:
    data = []
    ul = self.find(driver)
    rows = self.row.element.find_all(ul)
    for row in rows:
      year_match = re.match(r"(\d+)", row.text)
      year = year_match.group(1) if year_match else ""
      out = {
        "year": year,
        "1o_semestre_matematica": "",
        "1o_semestre_ingles": "",
        "2o_semestre_matematica": "",
        "2o_semestre_ingles": "",
      }
      links = row.find_elements(By.TAG_NAME, "a")
      for a in links:
        href = a.get_attribute("href")
        parsed = _parse_prova_href(href)
        if parsed:
          sem, tipo = parsed
          key = f"{sem}_{tipo}"
          if key in out:
            out[key] = href
      data.append(out)
    return data


DRIVER_PROVAS_POS_ITA = ITAProvasFlow(
  url=PROVAS_ITA_POS_URL,
  tables=[
    ProvasPosTable(
      element=BaseElement(
        identifier="/html/body/div[3]/ul[2]/ul",
        tag="ul",
      ),
      row=SeleniumTableRow(
        element=BaseElement(
          identifier=".//li",
          tag="li",
        ),
        item=SeleniumElement(
          element=BaseElement(
            identifier=".//a",
            tag="a",
          ),
        ),
      ),
    ),
  ],
)
