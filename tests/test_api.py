"""Testes da API contra um SQLite temporário construído do zero."""


async def test_health(api):
  async with api as client:
    assert (await client.get("/health")).status_code == 200


async def test_list_and_filter(api):
  async with api as client:
    body = (await client.get("/api/v1/questions")).json()
    assert body["total"] == 3
    assert {i["number"] for i in body["items"]} == {1, 2, 3}

    body = (await client.get("/api/v1/questions?discipline=matematica")).json()
    assert body["total"] == 2

    body = (await client.get("/api/v1/questions?discipline=fisica")).json()
    assert [i["number"] for i in body["items"]] == [2]


async def test_full_text_search_folds_accents(api):
  async with api as client:
    body = (await client.get("/api/v1/questions?q=aceleracao")).json()
    assert body["total"] == 1, "busca sem acento deveria achar 'aceleração'"
    assert body["items"][0]["number"] == 2
    assert "<mark>" in body["items"][0]["excerpt"]


async def test_search_survives_hostile_syntax(api):
  """A entrada do usuário chega na sintaxe do FTS5; sem sanitização isso é 500."""
  async with api as client:
    for query in ['a"b(', "*", "NEAR(", "^)"]:
      response = await client.get("/api/v1/questions", params={"q": query})
      assert response.status_code == 200, f"quebrou com {query!r}"


async def test_has_answer_filter(api):
  async with api as client:
    body = (await client.get("/api/v1/questions?has_answer=true")).json()
    assert [i["number"] for i in body["items"]] == [1]
    body = (await client.get("/api/v1/questions?has_answer=false")).json()
    assert {i["number"] for i in body["items"]} == {2, 3}


async def test_detail_includes_alternatives_and_source(api):
  async with api as client:
    listing = (await client.get("/api/v1/questions?discipline=matematica")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    body = (await client.get(f"/api/v1/questions/{qid}")).json()
    assert body["statement_raw"].startswith("Seja z")
    assert [a["letter"] for a in body["alternatives"]] == list("ABCDE")
    assert [a["letter"] for a in body["alternatives"] if a["is_correct"]] == ["C"]
    assert body["source"]["local_path"].endswith("prova.pdf")


async def test_random_route_precedes_id_route(api):
  """Declarada depois de /{question_id}, a rota /random viraria 422."""
  async with api as client:
    response = await client.get("/api/v1/questions/random")
    assert response.status_code == 200
    assert response.json()["number"] in {1, 2, 3}


async def test_image_serving_and_bounds(api):
  async with api as client:
    listing = (await client.get("/api/v1/questions?discipline=matematica")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)

    ok = await client.get(f"/api/v1/questions/{qid}/image")
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "image/png"

    assert (
      await client.get(f"/api/v1/questions/{qid}/image?part=99")
    ).status_code == 404


async def test_validation_and_not_found(api):
  async with api as client:
    assert (await client.get("/api/v1/questions/999999")).status_code == 404
    assert (await client.get("/api/v1/questions?order=relevance")).status_code == 422
    assert (await client.get("/api/v1/questions?page_size=500")).status_code == 422


async def test_pagination_envelope(api):
  async with api as client:
    body = (await client.get("/api/v1/questions?page_size=2&page=2")).json()
    assert body["total"] == 3
    assert body["pages"] == 2
    assert len(body["items"]) == 1


async def test_exams_and_stats(api):
  async with api as client:
    exams = (await client.get("/api/v1/exams")).json()
    assert exams[0]["slug"] == "vestibular-2025-1f"
    assert exams[0]["question_count"] == 3

    stats = (await client.get("/api/v1/stats")).json()
    assert stats["questions"] == 3
    assert stats["with_answer"] == 1
    assert {d["discipline"]: d["count"] for d in stats["by_discipline"]} == {
      "matematica": 2,
      "fisica": 1,
    }
