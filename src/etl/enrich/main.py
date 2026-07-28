"""Estágio de enriquecimento: normaliza questões com o LLM.

Roda entre o transform e o load, é cacheado em disco e resumível. Cada resultado
é gravado no instante em que chega, então uma queda na questão 47 de 60 custa
uma questão, não o lote.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src.config import settings
from src.etl.enrich.cache import EnrichCache, cache_key
from src.etl.enrich.client import (
  DEFAULT_EFFORT,
  MissingApiKey,
  build_client,
  count_input_tokens,
  normalize_question,
)
from src.etl.enrich.costs import CostTracker, estimate_total
from src.etl.enrich.prompt import PROMPT_VERSION
from src.etl.load.reader import iter_processed
from src.etl.transform.paths import ENRICHED_SUBDIR

# Amostra usada pelo --dry-run para extrapolar o custo do lote.
DRY_RUN_SAMPLE = 5
# Saída típica por questão: enunciado em LaTeX, alternativas e o thinking,
# que no Opus 5 vem ligado por padrão e também conta como saída. Por isso o
# número varia tanto com o effort — é o principal fator do custo.
ESTIMATED_OUTPUT_TOKENS = {
  "low": 1100,
  "medium": 1600,
  "high": 2200,
  "xhigh": 3000,
  "max": 4000,
}


@dataclass
class QuestionJob:
  slug: str
  directory: Path
  number: int
  source_sha256: str
  images: list[Path]
  raw_text: str
  support_text: str

  @property
  def target(self) -> Path:
    return self.directory / ENRICHED_SUBDIR / f"questao_{self.number:02d}.json"


@dataclass
class EnrichStats:
  processed: int = 0
  cached: int = 0
  failed: int = 0
  skipped_no_image: int = 0
  errors: list[str] = field(default_factory=list)

  def summary(self) -> str:
    return (
      f"{self.processed} normalizadas, {self.cached} do cache, "
      f"{self.failed} falhas, {self.skipped_no_image} sem imagem"
    )


def _read(path: Path) -> str:
  return path.read_text(encoding="utf-8") if path.exists() else ""


def collect_jobs(only: str | None = None) -> list[QuestionJob]:
  jobs: list[QuestionJob] = []
  for doc in iter_processed():
    manifest = doc.manifest
    if manifest is None:
      continue
    if only and only not in manifest.get("slug", ""):
      continue

    directory = doc.directory
    for entry in manifest.get("questions", []):
      images = [
        directory / part["image"]
        for part in entry.get("parts", [])
        if part.get("image")
      ]
      images = [p for p in images if p.exists()]
      jobs.append(
        QuestionJob(
          slug=manifest["slug"],
          directory=directory,
          number=entry["number"],
          source_sha256=entry["source_sha256"],
          images=images,
          raw_text=_read(directory / entry["text_inside"])
          if entry.get("text_inside")
          else "",
          support_text=_read(directory / entry["text_outside"])
          if entry.get("text_outside")
          else "",
        )
      )
  return jobs


def _write_result(job: QuestionJob, payload: dict) -> None:
  job.target.parent.mkdir(parents=True, exist_ok=True)
  job.target.write_text(
    json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
  )


def _cmd_dry_run(args: argparse.Namespace) -> int:
  jobs = collect_jobs(args.only)
  jobs = [j for j in jobs if j.images]
  if not jobs:
    print("nenhuma questao com imagem encontrada")
    return 1

  cache = EnrichCache()
  pending = [
    j
    for j in jobs
    if cache.get(cache_key(j.source_sha256, PROMPT_VERSION, settings.llm_model)) is None
  ]

  print(f"questoes totais:   {len(jobs)}")
  print(f"ja em cache:       {len(jobs) - len(pending)}")
  print(f"a processar:       {len(pending)}")
  if not pending:
    print("\nnada a fazer — tudo em cache")
    return 0

  try:
    client = build_client()
  except MissingApiKey as exc:
    print(f"\n{exc}", file=sys.stderr)
    return 1

  sample = pending[:DRY_RUN_SAMPLE]
  counts = [
    count_input_tokens(
      client,
      images=job.images,
      raw_text=job.raw_text,
      support_text=job.support_text,
    )
    for job in sample
  ]
  average = sum(counts) // len(counts)

  # A contagem inclui o prefixo de sistema, que na prática vem do cache a
  # partir da segunda chamada — descontá-lo evita superestimar.
  prefix = count_input_tokens(client, images=[], raw_text="", support_text=None)
  per_question = max(average - prefix, 0)

  output_tokens = ESTIMATED_OUTPUT_TOKENS[args.effort]
  total = estimate_total(
    model=settings.llm_model,
    questions=len(pending),
    input_tokens_per_question=per_question,
    output_tokens_per_question=output_tokens,
    cached_prefix_tokens=prefix,
  )

  print(f"\nmodelo:            {settings.llm_model}  (effort={args.effort})")
  print(f"tokens/questao:    ~{average} entrada ({prefix} de prefixo cacheado)")
  print(f"saida estimada:    ~{output_tokens} tokens/questao (inclui thinking)")
  print(f"\ncusto estimado:    US$ {total:.2f}")
  print(f"  (via Batch API:  US$ {total * 0.5:.2f})")
  print("\nestimativa a partir de uma amostra; rode com --limit primeiro.")
  return 0


def run_enrich(
  *,
  only: str | None = None,
  limit: int | None = None,
  force: bool = False,
  retry_failed: bool = False,
  max_cost: float | None = None,
  effort: str = DEFAULT_EFFORT,
  verbose: bool = True,
) -> EnrichStats:
  jobs = collect_jobs(only)
  cache = EnrichCache()
  stats = EnrichStats()
  tracker = CostTracker(model=settings.llm_model)
  client = None

  processed = 0
  for job in jobs:
    if not job.images:
      stats.skipped_no_image += 1
      continue

    key = cache_key(job.source_sha256, PROMPT_VERSION, settings.llm_model)
    entry = cache.get(key)

    if entry is not None and not force:
      stats.cached += 1
      _write_result(job, entry.payload)
      continue

    if cache.has_error(key) and not (retry_failed or force):
      stats.failed += 1
      continue

    if limit is not None and processed >= limit:
      break
    if max_cost is not None and tracker.usd >= max_cost:
      print(f"teto de custo atingido (US$ {tracker.usd:.2f}); parando", file=sys.stderr)
      break

    if client is None:
      client = build_client()

    result = normalize_question(
      client,
      images=job.images,
      raw_text=job.raw_text,
      support_text=job.support_text,
      effort=effort,
    )
    tracker.add(result.usage)
    processed += 1

    if result.question is None:
      stats.failed += 1
      message = result.error or "desconhecido"
      stats.errors.append(f"{job.slug} q{job.number}: {message}")
      cache.put_error(key, result.status, message)
      if verbose:
        print(f"  FALHA {job.slug} q{job.number}: {message}")
      continue

    payload = result.question.model_dump()
    # Grava imediatamente: uma queda no meio do lote não pode custar o que já
    # foi pago.
    cache.put(key, payload, result.usage)
    _write_result(job, payload)
    stats.processed += 1
    if verbose:
      confidence = payload["transcription_confidence"]
      print(f"  {job.slug} q{job.number:>3}  confianca={confidence}")

  if verbose and tracker.calls:
    print(f"\n{tracker.summary()}")
  return stats


def main() -> int:
  parser = argparse.ArgumentParser(description="Normaliza questões com o LLM.")
  parser.add_argument("--only", default=None, help="filtra por trecho do slug")
  parser.add_argument("--limit", type=int, default=None, help="máximo de chamadas")
  parser.add_argument("--force", action="store_true", help="ignora o cache")
  parser.add_argument(
    "--retry-failed", action="store_true", help="reprocessa apenas as que falharam"
  )
  parser.add_argument(
    "--max-cost", type=float, default=None, help="teto de gasto em USD"
  )
  parser.add_argument(
    "--effort",
    default=DEFAULT_EFFORT,
    choices=["low", "medium", "high", "xhigh", "max"],
    help="profundidade do raciocinio; principal controle de custo",
  )
  parser.add_argument(
    "--dry-run", action="store_true", help="estima o custo sem chamar o modelo"
  )
  parser.add_argument("--quiet", action="store_true")
  args = parser.parse_args()

  if args.dry_run:
    return _cmd_dry_run(args)

  try:
    stats = run_enrich(
      only=args.only,
      limit=args.limit,
      force=args.force,
      retry_failed=args.retry_failed,
      max_cost=args.max_cost,
      effort=args.effort,
      verbose=not args.quiet,
    )
  except MissingApiKey as exc:
    print(str(exc), file=sys.stderr)
    return 1

  print(f"\n{stats.summary()}")
  return 1 if stats.failed and not stats.processed else 0


if __name__ == "__main__":
  sys.exit(main())
