"""Endpoints e filtros que faltavam cobertura.

Concentra-se em três coisas que só aparecem exercitando a API: a renderização da
página com as regiões desenhadas (que é o que permite auditar a detecção), os
filtros combinados, e os caminhos de erro — 404, 422, ETag.
"""

import pytest

from src.etl.load.decisions import load_decisions

# --- imagem da página ------------------------------------------------------


async def test_page_image_renders_png(api):
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    response = await client.get(f"/api/v1/documents/{docs[0]['id']}/pages/0/image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert response.headers["ETag"]


async def test_page_image_honours_the_dpi_bounds(api):
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    base = f"/api/v1/documents/{docs[0]['id']}/pages/0/image"
    small = await client.get(f"{base}?dpi=40")
    assert small.status_code == 200
    # O teto existe para não gerar PNGs de vários MB a cada scroll.
    assert (await client.get(f"{base}?dpi=9999")).status_code == 422


async def test_page_image_with_and_without_boxes_differ(api):
  """Com `boxes=1` os retângulos são desenhados sobre a página."""
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    base = f"/api/v1/documents/{docs[0]['id']}/pages/0/image"
    plain = await client.get(f"{base}?boxes=0")
    boxed = await client.get(f"{base}?boxes=1")
    assert plain.content != boxed.content


async def test_highlight_changes_the_render(api):
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    base = f"/api/v1/documents/{docs[0]['id']}/pages/0/image?boxes=1"
    assert (await client.get(base)).content != (
      await client.get(f"{base}&highlight=1")
    ).content


async def test_page_out_of_range_is_404(api):
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    response = await client.get(f"/api/v1/documents/{docs[0]['id']}/pages/99/image")
    assert response.status_code == 404


async def test_page_image_of_a_missing_document_is_404(api):
  async with api as client:
    assert (await client.get("/api/v1/documents/9999/pages/0/image")).status_code == 404


async def test_empty_highlight_is_rejected(api):
  """`highlight=` vazio dava 422 e a interface o mandava sem questão selecionada."""
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    response = await client.get(
      f"/api/v1/documents/{docs[0]['id']}/pages/0/image?highlight="
    )
    assert response.status_code == 422


# --- progresso -------------------------------------------------------------


async def test_progress_totals_reconcile(api):
  async with api as client:
    progress = (await client.get("/api/v1/documents/progress")).json()
    assert (
      progress["reviewed"] + progress["rejected"] + progress["pending"]
      == (progress["total"])
    )


async def test_detailed_progress_slices_add_up(api):
  async with api as client:
    detail = (await client.get("/api/v1/documents/progress/detalhado")).json()
    total = detail["overall"]["total"]
    for key in ("by_test_type", "by_year", "by_phase", "by_discipline"):
      assert sum(bucket["total"] for bucket in detail[key]) == total, key
    assert detail["by_document"]


async def test_detailed_progress_counts_empty_text(api):
  async with api as client:
    detail = (await client.get("/api/v1/documents/progress/detalhado")).json()
    for bucket in detail["by_discipline"]:
      assert bucket["empty_text"] >= 0
      assert bucket["needs_llm"] >= 0


# --- filtros ---------------------------------------------------------------


@pytest.mark.parametrize(
  ("query", "expected"),
  [
    ("discipline=matematica", 2),
    ("discipline=fisica", 1),
    ("year=2025", 3),
    ("year=1999", 0),
    ("test_type=vestibular", 3),
    ("test_type=pos", 0),
    ("review_status=raw", 3),
    ("review_status=human_verified", 0),
  ],
)
async def test_question_filters(api, query, expected):
  async with api as client:
    listing = (await client.get(f"/api/v1/questions?{query}")).json()
    assert listing["total"] == expected


async def test_filters_combine_with_and(api):
  async with api as client:
    listing = (
      await client.get("/api/v1/questions?discipline=matematica&year=2025")
    ).json()
    assert listing["total"] == 2
    none = (
      await client.get("/api/v1/questions?discipline=matematica&year=1999")
    ).json()
    assert none["total"] == 0


async def test_pagination_envelope(api):
  """O envelope é `page`/`page_size`, não `limit`/`offset`."""
  async with api as client:
    first = (await client.get("/api/v1/questions?page=1&page_size=2")).json()
    assert len(first["items"]) == 2
    assert (first["total"], first["page"], first["page_size"], first["pages"]) == (
      3,
      1,
      2,
      2,
    )

    second = (await client.get("/api/v1/questions?page=2&page_size=2")).json()
    assert len(second["items"]) == 1
    assert {i["id"] for i in first["items"]}.isdisjoint(
      {i["id"] for i in second["items"]}
    )


async def test_page_size_has_a_ceiling(api):
  """Sem teto, um cliente pediria o corpus inteiro numa resposta."""
  async with api as client:
    assert (await client.get("/api/v1/questions?page_size=100000")).status_code == 422
    assert (await client.get("/api/v1/questions?page=0")).status_code == 422


async def test_search_finds_by_content(api):
  async with api as client:
    hit = (await client.get("/api/v1/questions?q=complexo")).json()
    assert hit["total"] >= 1
    miss = (await client.get("/api/v1/questions?q=palavraquenaoexiste")).json()
    assert miss["total"] == 0


async def test_search_ignores_accents(api):
  """`remove_diacritics 2` é o que faz "numero" achar "número"."""
  async with api as client:
    assert (await client.get("/api/v1/questions?q=numero")).json()["total"] >= 1


async def test_random_respects_filters(api):
  async with api as client:
    for _ in range(6):
      question = (await client.get("/api/v1/questions/random?discipline=fisica")).json()
      assert question["discipline"] == "fisica"


async def test_random_with_no_match_is_404(api):
  async with api as client:
    assert (await client.get("/api/v1/questions/random?year=1999")).status_code == 404


# --- exames e estatísticas -------------------------------------------------


async def test_exams_listing_and_detail(api):
  async with api as client:
    exams = (await client.get("/api/v1/exams")).json()
    assert exams
    slug = exams[0]["slug"] if isinstance(exams, list) else exams["items"][0]["slug"]
    detail = await client.get(f"/api/v1/exams/{slug}")
    assert detail.status_code == 200
    assert detail.json()["question_count"] == 3


async def test_unknown_exam_is_404(api):
  async with api as client:
    assert (await client.get("/api/v1/exams/nao-existe")).status_code == 404


async def test_stats_reports_coverage(api):
  async with api as client:
    stats = (await client.get("/api/v1/stats")).json()
    assert stats["questions"] == 3


# --- imagem do recorte ----------------------------------------------------


async def test_question_image_has_an_etag(api):
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    response = await client.get(f"/api/v1/questions/{qid}/image")
    assert response.status_code == 200
    assert response.headers.get("ETag")


async def test_question_without_image_is_404(api):
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 2)
    assert (await client.get(f"/api/v1/questions/{qid}/image")).status_code == 404


async def test_missing_question_is_404(api):
  async with api as client:
    assert (await client.get("/api/v1/questions/9999")).status_code == 404


# --- revisão: caminhos que faltavam ---------------------------------------


async def test_manual_text_marks_human_verified(api, review_paths):
  """Corrigir à mão dispensa o LLM e blinda a linha contra o upsert."""
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = listing["items"][0]["id"]
    response = await client.post(
      f"/api/v1/questions/{qid}/review",
      json={
        "statement_markdown": "Seja $z$ com $|z| = 1$.",
        "alternatives": {"A": "$1$", "B": "$-1$"},
      },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "human_verified"

    detail = (await client.get(f"/api/v1/questions/{qid}")).json()
    assert detail["statement_markdown"] == "Seja $z$ com $|z| = 1$."
    by_letter = {a["letter"]: a for a in detail["alternatives"]}
    assert by_letter["A"]["text_markdown"] == "$1$"
    assert by_letter["A"]["review_status"] == "human_verified"

  decisions = load_decisions(review_paths)
  entry = next(iter(decisions.values()))
  assert entry["alternatives"] == {"A": "$1$", "B": "$-1$"}


async def test_recrop_regenerates_the_asset(api, review_paths):
  """O recorte é a definição da questão: mudá-lo reextrai o texto."""
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    before = (await client.get(f"/api/v1/questions/{qid}")).json()

    response = await client.post(
      f"/api/v1/questions/{qid}/review",
      json={
        "bbox": {
          "page_no": 0,
          "part_index": 0,
          "x0": 40,
          "y0": 40,
          "x1": 560,
          "y1": 200,
        }
      },
    )
    assert response.status_code == 200
    assert response.json()["recropped"] is True

    after = (await client.get(f"/api/v1/questions/{qid}")).json()
    assert after["pages"][0]["bbox"] == [40.0, 40.0, 560.0, 200.0]
    assert after["statement_raw"] != before["statement_raw"]

  assert next(iter(load_decisions(review_paths).values()))["bbox"]["x1"] == 560


async def test_recrop_with_a_degenerate_rectangle_is_422(api):
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    response = await client.post(
      f"/api/v1/questions/{qid}/review",
      json={
        "bbox": {"page_no": 0, "part_index": 0, "x0": 40, "y0": 40, "x1": 41, "y1": 41}
      },
    )
    assert response.status_code == 422


async def test_recrop_of_a_question_without_asset_reports_false(api):
  """A questão 2 do fixture não tem asset: não há o que regerar."""
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 2)
    response = await client.post(
      f"/api/v1/questions/{qid}/review",
      json={
        "bbox": {
          "page_no": 0,
          "part_index": 0,
          "x0": 40,
          "y0": 40,
          "x1": 560,
          "y1": 200,
        }
      },
    )
    assert response.status_code == 200
    assert response.json()["recropped"] is False


async def test_review_of_a_missing_question_is_404(api):
  async with api as client:
    response = await client.post(
      "/api/v1/questions/9999/review", json={"status": "rejected"}
    )
    assert response.status_code == 404


async def test_needs_llm_override_persists(api, review_paths):
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = listing["items"][0]["id"]
    await client.post(f"/api/v1/questions/{qid}/review", json={"needs_llm": True})
    assert (await client.get(f"/api/v1/questions/{qid}")).json()["needs_llm"] is True
  assert next(iter(load_decisions(review_paths).values()))["needs_llm"] is True


async def test_review_ui_pages_are_served(api):
  async with api as client:
    for path in ("/review", "/review/progresso"):
      response = await client.get(path)
      assert response.status_code == 200
      assert "text/html" in response.headers["content-type"]


async def test_document_answers_endpoint(api):
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    answers = (await client.get(f"/api/v1/documents/{docs[0]['id']}/answers")).json()
    assert isinstance(answers, list)


async def test_health(api):
  async with api as client:
    assert (await client.get("/health")).json()["status"] == "ok"
