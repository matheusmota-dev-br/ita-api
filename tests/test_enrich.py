"""Enrich: cache, custo, triagem e schema — sem chamar nenhum LLM.

**Nenhum teste aqui faz requisição de rede.** O que é exercitado é a lógica que
decide *se* vale chamar, *quanto* custaria e *o que fazer com a resposta* — que é
justamente a parte onde um erro custa dinheiro. O cliente é testado com um duplo
que devolve respostas prontas; a chave de API do ambiente nunca é usada.
"""

import json
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from src.etl.enrich.cache import EnrichCache, cache_key
from src.etl.enrich.costs import (
  BATCH_MULTIPLIER,
  CACHE_READ_MULTIPLIER,
  CostTracker,
  estimate_total,
  price_for,
)
from src.etl.enrich.prompt import (
  MIN_CACHEABLE_TOKENS,
  PROMPT_VERSION,
  SYSTEM_PROMPT,
  build_user_block,
)
from src.etl.enrich.schema import NormalizedQuestion
from src.etl.enrich.triage import dirtiness, triage

# --- cache -----------------------------------------------------------------


def test_cache_key_changes_with_prompt_and_model():
  """Bumpar o prompt tem que invalidar o cache sem apagar arquivo nenhum."""
  base = cache_key("abc", "1", "claude-opus-5")
  assert base != cache_key("abc", "2", "claude-opus-5")
  assert base != cache_key("abc", "1", "claude-sonnet-5")
  assert base != cache_key("outro", "1", "claude-opus-5")
  assert base == cache_key("abc", "1", "claude-opus-5")


def test_cache_roundtrip(tmp_path):
  cache = EnrichCache(tmp_path)
  cache.put("deadbeef", {"statement_markdown": "$x$"}, {"input_tokens": 10})
  entry = cache.get("deadbeef")
  assert entry.payload["statement_markdown"] == "$x$"
  assert entry.usage["input_tokens"] == 10


def test_cache_miss_is_none(tmp_path):
  assert EnrichCache(tmp_path).get("nao-existe") is None


def test_cache_shards_by_prefix(tmp_path):
  """Milhares de arquivos num diretório só ficam lentos de listar."""
  cache = EnrichCache(tmp_path)
  cache.put("ab" + "0" * 62, {}, {})
  assert (tmp_path / "ab").is_dir()


def test_success_clears_a_previous_error(tmp_path):
  cache = EnrichCache(tmp_path)
  cache.put_error("k", "invalid", "schema fora do contrato")
  assert cache.has_error("k")
  cache.put("k", {"ok": True}, {})
  assert not cache.has_error("k")


def test_stats_separates_errors_from_hits(tmp_path):
  cache = EnrichCache(tmp_path)
  cache.put("aa", {}, {})
  cache.put("bb", {}, {})
  cache.put_error("cc", "refusal", "recusou")
  assert cache.stats() == {"cached": 2, "errors": 1}


# --- custo -----------------------------------------------------------------


def test_price_lookup_falls_back_to_opus():
  assert price_for("claude-opus-5") == price_for("claude-opus-5-qualquer-sufixo")
  assert price_for("modelo-desconhecido") == price_for("claude-opus-5")


def test_tracker_accumulates_and_prices():
  tracker = CostTracker(model="claude-opus-5")
  tracker.add({"input_tokens": 1_000_000, "output_tokens": 0})
  price_in, _ = price_for("claude-opus-5")
  assert tracker.calls == 1
  assert tracker.usd == pytest.approx(price_in)


def test_cached_input_is_cheaper():
  """Ler do cache custa uma fração; é o que torna reexecutar barato."""
  fresh = CostTracker(model="claude-opus-5")
  fresh.add({"input_tokens": 1_000_000})
  cached = CostTracker(model="claude-opus-5")
  cached.add({"cache_read_input_tokens": 1_000_000})
  assert cached.usd == pytest.approx(fresh.usd * CACHE_READ_MULTIPLIER)


def test_tracker_tolerates_missing_and_null_usage_fields():
  tracker = CostTracker(model="claude-opus-5")
  tracker.add({})
  tracker.add({"input_tokens": None, "output_tokens": None})
  assert tracker.calls == 2
  assert tracker.usd == 0


def test_output_dominates_the_bill():
  """A medição do corpus disse que a saída é 87% da conta, não a entrada."""
  price_in, price_out = price_for("claude-opus-5")
  assert price_out > price_in


def test_summary_mentions_calls_and_dollars():
  tracker = CostTracker(model="claude-opus-5")
  tracker.add({"input_tokens": 100, "output_tokens": 200})
  summary = tracker.summary()
  assert "1 chamadas" in summary
  assert "US$" in summary


def test_estimate_scales_linearly_with_questions():
  kwargs = dict(
    model="claude-opus-5",
    input_tokens_per_question=1000,
    output_tokens_per_question=2000,
    cached_prefix_tokens=800,
  )
  one = estimate_total(questions=1, **kwargs)
  hundred = estimate_total(questions=100, **kwargs)
  assert hundred == pytest.approx(one * 100)
  assert one > 0


def test_batch_multiplier_halves_the_bill():
  assert BATCH_MULTIPLIER == 0.5


# --- triagem ---------------------------------------------------------------


def test_clean_text_is_not_sent():
  clean = (
    "Questão 15. Quando aquecido ao ar, 1,65 g de um elemento X forma "
    "2,29 g de um óxido de fórmula X3O4."
  )
  assert not triage(clean).needs_llm


def test_corrupted_math_is_sent():
  assert triage("1. Se x̸ = 0 e x = p6xy −9y2, ent˜ao, em termos de y, x =").needs_llm


def test_ocr_is_always_sent():
  """O Tesseract lê `x³` como `x)`: parece limpo e está errado."""
  assert triage("Questão 8. Seja p(x) = x) + bx? + cx + d.", from_ocr=True).needs_llm


def test_empty_text_is_sent_with_a_reason():
  assert triage("").needs_llm
  assert triage("   ").reason == "sem texto"


def test_dirtiness_orders_texts_sensibly():
  clean = "A energia cinetica do bloco vale um joule por segundo ao quadrado."
  dirty = "A energia ¸cin´etica «do bloco vale ˜um joule : : : ao quadrado."
  assert dirtiness(dirty) > dirtiness(clean)


def test_triage_gives_a_reason_when_it_sends():
  result = triage("x «6 „ x −2 x «5 ent˜ao")
  assert result.needs_llm
  assert result.reason


# --- prompt e schema -------------------------------------------------------


def test_system_prompt_is_long_enough_to_cache():
  """Prompt caching no Opus 5 exige um prefixo mínimo de 512 tokens.

  Abaixo disso o `cache_control` é aceito e não faz nada: cada chamada paga o
  prefixo inteiro. Este teste existe porque o prompt já esteve em ~484 tokens,
  logo abaixo do limite, e nada acusava.
  """
  approx_tokens = len(SYSTEM_PROMPT) / 4
  assert approx_tokens > MIN_CACHEABLE_TOKENS, (
    f"prompt com ~{approx_tokens:.0f} tokens; o cache exige {MIN_CACHEABLE_TOKENS}"
  )


def test_system_prompt_describes_the_corpus_formats():
  """O modelo não deve ter que adivinhar o que foi medido no corpus."""
  for marker in ["A ( )", "dissertative", "Convenções", "lado a lado"]:
    assert marker in SYSTEM_PROMPT, f"o prompt não menciona {marker!r}"


def test_prompt_version_is_declared():
  assert PROMPT_VERSION


def test_user_block_includes_the_raw_text():
  block = build_user_block("Questão 1. Enunciado bruto.")
  assert "Enunciado bruto" in block


def test_user_block_includes_support_text_when_given():
  """O bloco "Convenções" é contexto obrigatório das questões da seção."""
  block = build_user_block("enunciado", "Convenções: use g = 10 m/s².")
  assert "g = 10" in block


VALID_ANSWER = {
  "question_number": 1,
  "question_type": "objective",
  "statement_markdown": "Seja $z$ um complexo.",
  "alternatives": [{"letter": "A", "text_markdown": "$1$", "is_figure_only": False}],
  "detected_discipline": "matematica",
  "detected_topics": ["numeros-complexos"],
  "has_figure": False,
  "figure_descriptions": [],
  "transcription_confidence": "high",
  "notes": "",
}


def test_schema_accepts_a_well_formed_answer():
  parsed = NormalizedQuestion.model_validate(VALID_ANSWER)
  assert parsed.alternatives[0].letter == "A"
  assert parsed.detected_discipline == "matematica"
  assert parsed.question_type == "objective"


def test_schema_forbids_unknown_fields():
  """`extra="forbid"` é o que faz o structured output ser um contrato."""
  with pytest.raises(ValidationError):
    NormalizedQuestion.model_validate({**VALID_ANSWER, "campo_inventado": 1})


def test_schema_requires_every_field():
  """Nenhum campo opcional: um campo ausente é erro, não default silencioso."""
  with pytest.raises(ValidationError):
    NormalizedQuestion.model_validate({"statement_markdown": "x"})


def test_dissertative_has_no_alternatives():
  parsed = NormalizedQuestion.model_validate(
    {**VALID_ANSWER, "question_type": "dissertative", "alternatives": []}
  )
  assert parsed.alternatives == []


def test_schema_rejects_an_invalid_letter():
  """A saída estruturada é validada: uma letra fora de A–E não passa."""
  with pytest.raises(ValidationError):
    NormalizedQuestion.model_validate(
      {**VALID_ANSWER, "alternatives": [{"letter": "Z", "text_markdown": "y"}]}
    )


def test_schema_survives_a_roundtrip_through_json():
  """O cache guarda JSON; o que sai dele tem que voltar a validar."""
  parsed = NormalizedQuestion.model_validate(VALID_ANSWER)
  again = NormalizedQuestion.model_validate(json.loads(parsed.model_dump_json()))
  assert again == parsed


# --- cliente, com duplo ----------------------------------------------------


@dataclass
class FakeUsage:
  input_tokens: int = 100
  output_tokens: int = 200
  cache_read_input_tokens: int = 0
  cache_creation_input_tokens: int = 0


def test_missing_api_key_is_a_named_error(monkeypatch):
  """Falhar cedo e com nome, em vez de um 401 no meio de um lote de 2000."""
  from src.etl.enrich.client import MissingApiKey, build_client

  monkeypatch.setattr("src.config.settings.anthropic_api_key", None)
  monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
  with pytest.raises(MissingApiKey):
    build_client()


def test_image_block_encodes_the_png(tmp_path):
  from src.etl.enrich.client import _image_block

  png = tmp_path / "c.png"
  png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
  block = _image_block(png)
  assert block["type"] == "image"
  assert block["source"]["media_type"] == "image/png"
  assert block["source"]["data"]


def test_system_block_marks_the_cache_breakpoint():
  from src.etl.enrich.client import _system

  blocks = _system()
  assert any("cache_control" in b for b in blocks), (
    "sem o breakpoint o prompt de sistema e recobrado a cada questao"
  )
