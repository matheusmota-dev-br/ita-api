from dataclasses import dataclass, field

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement


@dataclass
class BaseElement:
  name: str | None = None
  identifier: str = ""
  identifier_type: By = By.XPATH
  tag: str | None = None

  def find(self, ctx) -> WebElement:
    return ctx.find_element(self.identifier_type, self.identifier)

  def find_all(self, ctx) -> list[WebElement]:
    return ctx.find_elements(self.identifier_type, self.identifier)

  def click(self, ctx) -> None:
    self.find(ctx).click()

  def send_keys(self, ctx, keys: str) -> None:
    self.find(ctx).send_keys(keys)


@dataclass
class SeleniumElement(BaseElement):
  element: BaseElement = None
  iframes: list[BaseElement] = field(default_factory=list)

  def _resolve_context(self, driver: WebDriver):
    driver.switch_to.default_content()

    if self.iframes:
      for iframe in self.iframes:
        if iframe is not None:
          driver.switch_to.frame(iframe.find(driver))

    return driver

  def find(self, driver: WebDriver) -> WebElement:
    context = self._resolve_context(driver)
    return self.element.find(context)

  def click(self, driver: WebDriver) -> None:
    context = self._resolve_context(driver)
    try:
      self.find(context).click()
    except WebDriverException:
      # Elemento coberto por outro nó ou fora da viewport: o clique via JS
      # ignora hit-testing e funciona nos frames antigos do site do ITA.
      element = self.find(context)
      driver.execute_script("arguments[0].click()", element)

  def send_keys(self, driver: WebDriver, keys: str) -> None:
    self.find(driver).send_keys(keys)


@dataclass
class SeleniumTableRow(SeleniumElement):
  item: SeleniumElement = None
  skip_first: bool = False


@dataclass
class SeleniumTable(SeleniumElement):
  row: SeleniumTableRow = None
  headers: list[str] = field(default_factory=list)

  def get_data(self, driver: WebDriver) -> list[dict[str, str]]:
    data = []
    element = self.find(driver)
    rows = self.row.element.find_all(element)
    if self.row.skip_first:
      rows = rows[1:]
    for row in rows:
      items = self.row.item.element.find_all(row)
      row_data = []
      for item in items:
        links = item.find_elements(By.TAG_NAME, "a")
        if links:
          row_data.append(links[0].get_attribute("href") or item.text)
        else:
          row_data.append(item.text)
      data.append(row_data)
    structured_data = [dict(zip(self.headers, row)) for row in data]
    return structured_data


@dataclass
class ITAProvasFlow:
  url: str
  navigation_steps: list[SeleniumElement] = field(default_factory=list)
  tables: list[SeleniumTable] = None

  def navigate(self, driver: WebDriver) -> None:
    for step in self.navigation_steps:
      step.click(driver)
