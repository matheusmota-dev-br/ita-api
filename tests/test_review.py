"""Endpoints da interface de auditoria e persistência da revisão."""

import json

import pytest

from src.etl.load.decisions import load_decisions, record_decision


async def test_documents_listing(api):
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    assert len(docs) == 1
    doc = docs[0]
    assert doc["exam_slug"] == "vestibular-2025-1f"
    assert doc["question_count"] == 3
    assert doc["with_answer_count"] == 1
    assert doc["reviewed_count"] == 0


async def test_document_question_ids_are_ordered(api):
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    ids = (await client.get(f"/api/v1/documents/{docs[0]['id']}/questions")).json()
    assert len(ids) == 3
    numbers = [
      (await client.get(f"/api/v1/questions/{qid}")).json()["number"] for qid in ids
    ]
    assert numbers == sorted(numbers)


async def test_review_updates_status_and_counters(api, tmp_path, monkeypatch):
  monkeypatch.setattr(
    "src.routers.review.record_decision",
    lambda **kw: record_decision(**kw, path=tmp_path / "decisions.json"),
  )
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    qid = (await client.get(f"/api/v1/documents/{docs[0]['id']}/questions")).json()[0]

    response = await client.post(
      f"/api/v1/questions/{qid}/review",
      json={"status": "human_verified", "note": "conferido"},
    )
    assert response.status_code == 200

    detail = (await client.get(f"/api/v1/questions/{qid}")).json()
    assert detail["review_status"] == "human_verified"

    progress = (await client.get("/api/v1/documents/progress")).json()
    assert progress["reviewed"] == 1
    assert progress["pending"] == 2


async def test_review_persists_outside_the_database(tmp_path):
  """A decisão precisa sobreviver a um --rebuild, que apaga o arquivo do banco.

  A chave é (caminho do PDF, número), não o id autoincremento, justamente para
  continuar válida depois que a tabela é recriada do zero.
  """
  path = tmp_path / "decisions.json"
  record_decision(
    local_path="data/x/prova.pdf", number=7, status="human_verified", path=path
  )
  record_decision(local_path="data/x/prova.pdf", number=8, status="rejected", path=path)
  # Regravar o mesmo item substitui, não duplica.
  record_decision(local_path="data/x/prova.pdf", number=7, status="rejected", path=path)

  decisions = load_decisions(path)
  assert len(decisions) == 2
  assert decisions["data/x/prova.pdf#7"]["status"] == "rejected"
  assert "reviewed_at" in decisions["data/x/prova.pdf#7"]

  # O arquivo é JSON legível e ordenado, para poder ser versionado e revisado.
  raw = json.loads(path.read_text(encoding="utf-8"))
  assert list(raw) == sorted(raw)


async def test_review_on_missing_question(api):
  async with api as client:
    response = await client.post(
      "/api/v1/questions/999999/review", json={"status": "human_verified"}
    )
    assert response.status_code == 404


async def test_review_rejects_invalid_status(api):
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    qid = (await client.get(f"/api/v1/documents/{docs[0]['id']}/questions")).json()[0]
    response = await client.post(
      f"/api/v1/questions/{qid}/review", json={"status": "talvez"}
    )
    assert response.status_code == 422


async def test_review_ui_is_served(api):
  async with api as client:
    response = await client.get("/review")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # A página importa os arquivos vendorizados, não um CDN.
    body = response.text
    assert "/static/vendor/react.js" in body
    assert "esm.sh" not in body and "unpkg" not in body


@pytest.mark.parametrize("name", ["react.js", "react-dom.js", "htm.js"])
def test_vendor_files_are_self_contained(name):
  from src.routers.review import STATIC_DIR

  source = (STATIC_DIR / "vendor" / name).read_text(encoding="utf-8")
  assert source.strip(), f"{name} vazio"
  # Nenhum import pode apontar para fora: a interface tem que abrir offline.
  for token in ('from"http', 'from "http', 'import"http', 'import "http'):
    assert token not in source, f"{name} importa de um host externo"
