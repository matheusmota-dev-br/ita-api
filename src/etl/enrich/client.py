"""Cliente Anthropic para a normalização de questões."""

import base64
from dataclasses import dataclass
from pathlib import Path

import anthropic

from src.config import settings
from src.etl.enrich.prompt import SYSTEM_PROMPT, build_user_block
from src.etl.enrich.schema import NormalizedQuestion

# O thinking é ligado por padrão no Opus 5 e `max_tokens` limita thinking MAIS
# resposta. Um valor apertado trunca a transcrição no meio.
MAX_TOKENS = 16000

# O `effort` é o principal controle de custo, porque o thinking conta como
# saída e a saída é ~85% da conta. Transcrever não é uma tarefa que exija
# raciocínio profundo, então "medium" costuma bastar; "high" é o padrão da
# API e custa cerca de 30% a mais.
DEFAULT_EFFORT = "medium"

# Erros que a própria SDK já repete; aqui só entram no relatório se persistirem.
RETRYABLE_ERRORS = (
  anthropic.RateLimitError,
  anthropic.APIConnectionError,
  anthropic.InternalServerError,
)
# Erros de um item só: registram e o lote segue.
PER_ITEM_ERRORS = (anthropic.BadRequestError, anthropic.UnprocessableEntityError)
# Erros que não adianta repetir 2000 vezes.
FATAL_ERRORS = (anthropic.AuthenticationError, anthropic.PermissionDeniedError)


class MissingApiKey(RuntimeError):
  pass


@dataclass
class LLMResult:
  question: NormalizedQuestion | None
  usage: dict
  status: str
  error: str | None = None


def build_client() -> anthropic.Anthropic:
  if not settings.anthropic_api_key:
    raise MissingApiKey(
      "ANTHROPIC_API_KEY nao configurada; defina no .env para rodar o enrich"
    )
  # max_retries alto porque a SDK já faz backoff exponencial em 429/5xx.
  return anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=5)


def _image_block(path: Path) -> dict:
  data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
  return {
    "type": "image",
    "source": {"type": "base64", "media_type": "image/png", "data": data},
  }


def _messages(
  images: list[Path], raw_text: str, support_text: str | None
) -> list[dict]:
  # A imagem vem antes do texto: é a fonte da verdade, e o texto é só apoio.
  content: list[dict] = [_image_block(p) for p in images]
  content.append({"type": "text", "text": build_user_block(raw_text, support_text)})
  return [{"role": "user", "content": content}]


def _system() -> list[dict]:
  # cache_control no bloco de sistema: é o prefixo estável de toda chamada.
  # O mínimo cacheável no Opus 5 é 512 tokens, que este prompt ultrapassa.
  return [
    {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
  ]


def normalize_question(
  client: anthropic.Anthropic,
  *,
  images: list[Path],
  raw_text: str,
  support_text: str | None = None,
  model: str | None = None,
  effort: str = DEFAULT_EFFORT,
) -> LLMResult:
  """Uma chamada. Nunca levanta por falha de item — devolve status no resultado."""
  model = model or settings.llm_model
  try:
    response = client.messages.parse(
      model=model,
      max_tokens=MAX_TOKENS,
      system=_system(),
      messages=_messages(images, raw_text, support_text),
      output_config={"effort": effort},
      output_format=NormalizedQuestion,
    )
  except FATAL_ERRORS:
    raise
  except RETRYABLE_ERRORS as exc:
    return LLMResult(None, {}, "api_error", f"{type(exc).__name__}: {exc}")
  except PER_ITEM_ERRORS as exc:
    return LLMResult(None, {}, "api_error", f"{type(exc).__name__}: {exc}")

  usage = {
    "input_tokens": response.usage.input_tokens,
    "output_tokens": response.usage.output_tokens,
    "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
    "cache_creation_input_tokens": getattr(
      response.usage, "cache_creation_input_tokens", 0
    ),
  }

  # Uma recusa dos classificadores volta como HTTP 200 com content vazio.
  # Ler content[0] sem checar stop_reason quebraria aqui.
  if response.stop_reason == "refusal":
    return LLMResult(None, usage, "refused", "classificadores recusaram a requisicao")

  if response.parsed_output is None:
    return LLMResult(None, usage, "schema_error", "resposta sem saida estruturada")

  return LLMResult(response.parsed_output, usage, "ok")


def count_input_tokens(
  client: anthropic.Anthropic,
  *,
  images: list[Path],
  raw_text: str,
  support_text: str | None = None,
  model: str | None = None,
) -> int:
  """Contagem real de tokens de entrada, para a estimativa do --dry-run."""
  response = client.messages.count_tokens(
    model=model or settings.llm_model,
    system=_system(),
    messages=_messages(images, raw_text, support_text),
  )
  return response.input_tokens
