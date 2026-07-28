from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class Page(BaseModel, Generic[T]):
  """Paginação por offset com total.

  O corpus tem alguns milhares de questões, então keyset não traria ganho — e
  não expressaria `ORDER BY bm25(...)` nem `ORDER BY RANDOM()`, que a API usa.
  """

  items: list[T]
  total: int
  page: int
  page_size: int
  pages: int

  @classmethod
  def build(cls, items: list[T], total: int, page: int, page_size: int) -> "Page[T]":
    return cls(
      items=items,
      total=total,
      page=page,
      page_size=page_size,
      pages=max(1, -(-total // page_size)),
    )
