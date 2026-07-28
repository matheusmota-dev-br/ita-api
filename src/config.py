from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
  model_config = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
  )

  # sqlite+aiosqlite para a API (async); database_url_sync deriva o dialeto síncrono
  # usado pelo Alembic e pelo ETL.
  database_url: str = "sqlite+aiosqlite:///./data/ita.db"

  # Raiz dos artefatos do ETL. media_root é o prefixo contra o qual assets.relative_path
  # é resolvido ao servir imagens — a checagem de containment depende disso.
  data_dir: Path = PROJECT_ROOT / "data"
  media_root: Path = PROJECT_ROOT / "data"

  # Onde a revisão humana é persistida: decisions.json, audit.jsonl e
  # baseline.json. Configurável porque é a única forma de um servidor de teste
  # exercitar os endpoints de auditoria sem escrever no arquivo de verdade — que
  # é versionado, e onde um teste de navegador já gravou operações que pareciam
  # auditoria real.
  review_dir: Path = PROJECT_ROOT / "data" / "review"

  anthropic_api_key: str | None = None
  llm_model: str = "claude-opus-5"

  # "fts5" ou "like": o repositório degrada para LIKE se o SQLite não tiver FTS5.
  search_backend: str = "fts5"

  @property
  def database_url_sync(self) -> str:
    return self.database_url.replace("sqlite+aiosqlite://", "sqlite://")

  @property
  def sqlite_path(self) -> Path | None:
    """Caminho do arquivo .db, ou None se a URL não for um sqlite baseado em arquivo."""
    url = self.database_url_sync
    if not url.startswith("sqlite:///"):
      return None
    raw = url[len("sqlite:///") :]
    if raw.startswith(":memory:") or raw.startswith("file:"):
      return None
    path = Path(raw)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


settings = Settings()


def ensure_sqlite_dir() -> None:
  """Cria o diretório do arquivo .db se ele ainda não existir.

  Chamado tanto por src.database quanto pelo env.py do Alembic — o Alembic não
  importa src.database, então não dá para deixar isso só lá.
  """
  path = settings.sqlite_path
  if path is not None:
    path.parent.mkdir(parents=True, exist_ok=True)
