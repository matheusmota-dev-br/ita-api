"""Estimativa e acompanhamento de custo."""

from dataclasses import dataclass, field

# USD por milhão de tokens. Claude Opus 5: $5 entrada / $25 saída.
# Leitura de cache custa ~0,1x a entrada; escrita de cache ~1,25x.
PRICING_PER_MTOK = {
  "claude-opus-5": (5.0, 25.0),
  "claude-sonnet-5": (3.0, 15.0),
  "claude-haiku-4-5": (1.0, 5.0),
}

CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25
# A Batch API cobra metade do preço.
BATCH_MULTIPLIER = 0.5


def price_for(model: str) -> tuple[float, float]:
  for prefix, prices in PRICING_PER_MTOK.items():
    if model.startswith(prefix):
      return prices
  return PRICING_PER_MTOK["claude-opus-5"]


@dataclass
class CostTracker:
  model: str
  input_tokens: int = 0
  output_tokens: int = 0
  cache_read_tokens: int = 0
  cache_write_tokens: int = 0
  calls: int = 0
  errors: list[str] = field(default_factory=list)

  def add(self, usage: dict) -> None:
    self.calls += 1
    self.input_tokens += usage.get("input_tokens", 0) or 0
    self.output_tokens += usage.get("output_tokens", 0) or 0
    self.cache_read_tokens += usage.get("cache_read_input_tokens", 0) or 0
    self.cache_write_tokens += usage.get("cache_creation_input_tokens", 0) or 0

  @property
  def usd(self) -> float:
    price_in, price_out = price_for(self.model)
    return (
      self.input_tokens * price_in
      + self.cache_read_tokens * price_in * CACHE_READ_MULTIPLIER
      + self.cache_write_tokens * price_in * CACHE_WRITE_MULTIPLIER
      + self.output_tokens * price_out
    ) / 1_000_000

  def summary(self) -> str:
    return (
      f"{self.calls} chamadas | entrada {self.input_tokens} "
      f"(cache: {self.cache_read_tokens} lidos, {self.cache_write_tokens} escritos) | "
      f"saida {self.output_tokens} | US$ {self.usd:.2f}"
    )


def estimate_total(
  *,
  model: str,
  questions: int,
  input_tokens_per_question: int,
  output_tokens_per_question: int,
  cached_prefix_tokens: int,
) -> float:
  """Custo projetado, assumindo que o prefixo de sistema é lido do cache."""
  price_in, price_out = price_for(model)
  per_question = (
    input_tokens_per_question * price_in
    + cached_prefix_tokens * price_in * CACHE_READ_MULTIPLIER
    + output_tokens_per_question * price_out
  ) / 1_000_000
  return per_question * questions
