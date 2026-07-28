from src.etl.enrich.cache import EnrichCache, cache_key
from src.etl.enrich.costs import CostTracker, estimate_total
from src.etl.enrich.main import EnrichStats, QuestionJob, collect_jobs, run_enrich
from src.etl.enrich.prompt import PROMPT_VERSION, SYSTEM_PROMPT, build_user_block
from src.etl.enrich.schema import NormalizedAlternative, NormalizedQuestion

# A função `main` não é reexportada de propósito: o nome sombrearia o submódulo
# de mesmo nome, e `import src.etl.enrich.main` passaria a devolver a
# função em vez do módulo. Quem precisa dela importa do módulo direto, como o
# __main__.py já faz.
__all__ = [
  "build_user_block",
  "cache_key",
  "collect_jobs",
  "CostTracker",
  "EnrichCache",
  "EnrichStats",
  "estimate_total",
  "NormalizedAlternative",
  "NormalizedQuestion",
  "PROMPT_VERSION",
  "QuestionJob",
  "run_enrich",
  "SYSTEM_PROMPT",
]
