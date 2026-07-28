"""Criar, excluir e renumerar questões — e a auditoria disso."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.etl.load.decisions import (
  OP_CREATE,
  OP_DELETE,
  OP_RENUMBER,
  load_decisions,
  load_operations,
  record_decision,
  record_operation,
)
from src.models import Question

BBOX = {"page_no": 0, "part_index": 0, "x0": 40.0, "y0": 40.0, "x1": 550.0, "y1": 300.0}


# --- endpoints -------------------------------------------------------------


async def test_create_question_from_region(api, review_paths):
  """A região marcada vira questão, com recorte e texto extraídos dela."""
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    document_id = docs[0]["id"]

    res = await client.post(
      f"/api/v1/documents/{document_id}/questions", json={"number": 9, "bbox": BBOX}
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["operation"] == OP_CREATE
    assert body["number"] == 9

    created = (await client.get(f"/api/v1/questions/{body['question_id']}")).json()
    # O texto sai do próprio retângulo: é o recorte que define a questão.
    assert "Questao 1" in created["statement_raw"]
    assert created["review_status"] == "human_verified"
    assert [a["letter"] for a in created["alternatives"]] == list("ABCDE")
    assert len(created["pages"]) == 1

  operations = load_operations(review_paths)
  assert [o["op"] for o in operations] == [OP_CREATE]
  assert operations[0]["bbox"] == BBOX


async def test_create_rejects_number_already_used(api):
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    res = await client.post(
      f"/api/v1/documents/{docs[0]['id']}/questions", json={"number": 1, "bbox": BBOX}
    )
    assert res.status_code == 409


async def test_create_rejects_degenerate_rectangle(api):
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    flat = {**BBOX, "y1": BBOX["y0"]}
    res = await client.post(
      f"/api/v1/documents/{docs[0]['id']}/questions", json={"number": 9, "bbox": flat}
    )
    assert res.status_code == 422


async def test_delete_question(api, review_paths):
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    target = next(i for i in listing["items"] if i["number"] == 2)

    res = await client.delete(f"/api/v1/questions/{target['id']}")
    assert res.status_code == 200
    assert res.json()["operation"] == OP_DELETE
    assert (await client.get(f"/api/v1/questions/{target['id']}")).status_code == 404

  assert [o["op"] for o in load_operations(review_paths)] == [OP_DELETE]


async def test_renumber_to_free_number(api, review_paths):
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    target = next(i for i in listing["items"] if i["number"] == 1)

    res = await client.post(
      f"/api/v1/questions/{target['id']}/number", json={"number": 40}
    )
    assert res.status_code == 200
    assert res.json()["swapped_with"] is None

    moved = (await client.get(f"/api/v1/questions/{target['id']}")).json()
    assert moved["number"] == 40

  op = load_operations(review_paths)[0]
  assert (op["op"], op["number"], op["to"]) == (OP_RENUMBER, 1, 40)


async def test_renumber_onto_occupied_number_swaps(api):
  """Trocar de ordem é o caso comum; falhar obrigaria a três passos."""
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    first = next(i for i in listing["items"] if i["number"] == 1)
    second = next(i for i in listing["items"] if i["number"] == 2)

    res = await client.post(
      f"/api/v1/questions/{first['id']}/number", json={"number": 2}
    )
    assert res.status_code == 200
    assert res.json()["swapped_with"] == 1

    assert (await client.get(f"/api/v1/questions/{first['id']}")).json()["number"] == 2
    assert (await client.get(f"/api/v1/questions/{second['id']}")).json()["number"] == 1


async def test_renumber_to_same_number_is_rejected(api):
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    target = next(i for i in listing["items"] if i["number"] == 1)
    res = await client.post(
      f"/api/v1/questions/{target['id']}/number", json={"number": 1}
    )
    assert res.status_code == 422


# --- persistência ----------------------------------------------------------


def test_renumber_moves_the_decision_with_the_question(tmp_path):
  """A chave da decisão é o número; renumerar sem movê-la perderia a correção."""
  path = tmp_path / "d.json"
  record_decision(
    local_path="p.pdf", number=5, statement_markdown="corrigido à mão", path=path
  )
  record_operation(op=OP_RENUMBER, local_path="p.pdf", number=5, to=6, path=path)

  decisions = load_decisions(path)
  assert "p.pdf#5" not in decisions
  assert decisions["p.pdf#6"]["statement_markdown"] == "corrigido à mão"
  assert decisions["p.pdf#6"]["number"] == 6


def test_swap_exchanges_both_decisions(tmp_path):
  path = tmp_path / "d.json"
  record_decision(local_path="p.pdf", number=1, note="era a um", path=path)
  record_decision(local_path="p.pdf", number=2, note="era a dois", path=path)
  record_operation(
    op=OP_RENUMBER, local_path="p.pdf", number=1, to=2, swap=True, path=path
  )

  decisions = load_decisions(path)
  assert decisions["p.pdf#2"]["note"] == "era a um"
  assert decisions["p.pdf#1"]["note"] == "era a dois"


def test_delete_discards_the_orphan_decision(tmp_path):
  path = tmp_path / "d.json"
  record_decision(local_path="p.pdf", number=3, status="rejected", path=path)
  record_operation(op=OP_DELETE, local_path="p.pdf", number=3, path=path)
  assert "p.pdf#3" not in load_decisions(path)


def test_operations_keep_their_order(tmp_path):
  """Renumerar 5→6 e depois 6→7 não é o mesmo que o contrário."""
  path = tmp_path / "d.json"
  record_operation(op=OP_RENUMBER, local_path="p.pdf", number=5, to=6, path=path)
  record_operation(op=OP_RENUMBER, local_path="p.pdf", number=6, to=7, path=path)
  assert [(o["number"], o["to"]) for o in load_operations(path)] == [(5, 6), (6, 7)]


def test_create_requires_a_rectangle(tmp_path):
  with pytest.raises(ValueError, match="retangulo"):
    record_operation(
      op=OP_CREATE, local_path="p.pdf", number=1, path=tmp_path / "d.json"
    )


def test_reads_the_v1_flat_format(tmp_path):
  """Arquivos gravados antes das operações precisam continuar sendo lidos."""
  path = tmp_path / "d.json"
  path.write_text(
    '{"p.pdf#1": {"local_path": "p.pdf", "number": 1, "status": "rejected"}}',
    encoding="utf-8",
  )
  assert load_decisions(path)["p.pdf#1"]["status"] == "rejected"
  assert load_operations(path) == []

  # E o primeiro registro novo converte o arquivo sem perder o que havia.
  record_operation(op=OP_DELETE, local_path="p.pdf", number=9, path=path)
  assert load_decisions(path)["p.pdf#1"]["status"] == "rejected"
  assert len(load_operations(path)) == 1


# --- replay no load --------------------------------------------------------


def _numbers(db_path) -> list[int]:
  engine = create_engine(f"sqlite:///{db_path}")
  with Session(engine) as session:
    return sorted(session.scalars(select(Question.number)))


async def test_operations_survive_a_rebuild(api, review_paths, tmp_path, monkeypatch):
  """O ponto inteiro da auditoria: um --rebuild não pode desfazer o trabalho."""
  # Importa o módulo, não o nome: `src.etl.load.main` como string resolve para
  # a função `main` reexportada no __init__, e o monkeypatch erraria o alvo.
  import src.etl.load.main as load_main
  from src.etl.load.main import LoadStats, _replay_operations

  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    document_id = docs[0]["id"]
    await client.post(
      f"/api/v1/documents/{document_id}/questions", json={"number": 9, "bbox": BBOX}
    )
    listing = (await client.get("/api/v1/questions")).json()
    victim = next(i for i in listing["items"] if i["number"] == 3)
    await client.delete(f"/api/v1/questions/{victim['id']}")

  assert [o["op"] for o in load_operations(review_paths)] == [OP_CREATE, OP_DELETE]

  # Simula o rebuild: o banco volta ao estado detectado pelo transform, e só o
  # replay das operações traz de volta o que foi feito à mão.
  db_path = tmp_path / "test.db"
  engine = create_engine(f"sqlite:///{db_path}")
  with Session(engine) as session:
    session.execute(select(Question))
    for question in session.scalars(select(Question).where(Question.number == 9)):
      session.delete(question)
    session.commit()
  assert 9 not in _numbers(db_path)

  monkeypatch.setattr(
    load_main, "load_operations", lambda: load_operations(review_paths)
  )
  stats = LoadStats()
  with Session(engine) as session:
    _replay_operations(session, stats)
    session.commit()

  assert stats.questions_created == 1
  assert 9 in _numbers(db_path), "a questão criada à mão tem que voltar"
  assert 3 not in _numbers(db_path), "a questão excluída não pode voltar"
