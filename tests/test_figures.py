"""Figuras anexadas a uma questão, impressas longe do enunciado.

Nas provas de pós a imagem fica solta numa página adiante da questão. Associar as
duas é decisão humana — não há como derivá-la do PDF — então precisa ser
registrada e reaplicada, como qualquer outra decisão de auditoria.

O que estes testes protegem: que anexar uma figura da página 9 a uma questão da
página 3 **não** mexa no retângulo do enunciado, e que a associação sobreviva a
um `--rebuild` sem duplicar.
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.etl.load.decisions import load_decisions
from src.models import Asset, Question
from src.models.enums import AssetKind
from tests.conftest import create_schema  # noqa: F401 - usado pelos fixtures

FAR_PAGE = {"page_no": 1, "part_index": 0, "x0": 40, "y0": 40, "x1": 550, "y1": 400}


async def _attach(client, question_id, bbox=None):
  return await client.post(
    f"/api/v1/questions/{question_id}/figuras", json={"bbox": bbox or FAR_PAGE}
  )


# --- anexar ----------------------------------------------------------------


async def test_attach_a_figure_from_another_page(api, review_paths):
  """O caso que motivou tudo: figura na página 2, questão na página 0."""
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    before = (await client.get(f"/api/v1/questions/{qid}")).json()

    response = await _attach(client, qid)
    assert response.status_code == 201, response.text
    figure = response.json()
    assert figure["page_no"] == 1
    assert figure["bbox"] == [40.0, 40.0, 550.0, 400.0]

    after = (await client.get(f"/api/v1/questions/{qid}")).json()
    assert len(after["figures"]) == 1
    # O ponto central: o retângulo do enunciado não se move.
    assert after["pages"] == before["pages"]
    assert after["source"]["page_start"] == before["source"]["page_start"]
    assert after["source"]["page_end"] == before["source"]["page_end"]
    assert after["statement_raw"] == before["statement_raw"]

  decision = next(iter(load_decisions(review_paths).values()))
  assert decision["figures"] == [FAR_PAGE]


async def test_figure_image_is_servable(api):
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    figure = (await _attach(client, qid)).json()

    image = await client.get(figure["image_url"])
    assert image.status_code == 200
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert image.headers["ETag"]


async def test_several_figures_accumulate(api, review_paths):
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)

    await _attach(client, qid, {**FAR_PAGE, "page_no": 0})
    await _attach(client, qid, {**FAR_PAGE, "page_no": 1})
    detail = (await client.get(f"/api/v1/questions/{qid}")).json()
    assert [f["page_no"] for f in detail["figures"]] == [0, 1]

  assert len(next(iter(load_decisions(review_paths).values()))["figures"]) == 2


async def test_figures_do_not_appear_as_question_parts(api):
  """`?part=N` serve o recorte do enunciado, não a figura anexada."""
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    await _attach(client, qid)

    detail = (await client.get(f"/api/v1/questions/{qid}")).json()
    assert len(detail["pages"]) == 1, "a figura nao pode virar uma parte da questao"


async def test_attach_with_a_degenerate_rectangle_is_422(api):
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    response = await _attach(client, qid, {**FAR_PAGE, "y1": FAR_PAGE["y0"]})
    assert response.status_code == 422


async def test_attach_beyond_the_last_page_is_422(api):
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    response = await _attach(client, qid, {**FAR_PAGE, "page_no": 99})
    assert response.status_code == 422


async def test_attach_to_a_missing_question_is_404(api):
  async with api as client:
    assert (await _attach(client, 9999)).status_code == 404


# --- desanexar -------------------------------------------------------------


async def test_detach_removes_the_figure_and_the_decision(api, review_paths):
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    figure = (await _attach(client, qid)).json()

    response = await client.delete(
      f"/api/v1/questions/{qid}/figuras/{figure['asset_id']}"
    )
    assert response.status_code == 204
    assert (await client.get(f"/api/v1/questions/{qid}")).json()["figures"] == []

  # A lista é substituída, não mesclada: senão a figura voltaria no rebuild.
  assert next(iter(load_decisions(review_paths).values()))["figures"] == []


async def test_detach_keeps_the_others(api, review_paths):
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    first = (await _attach(client, qid, {**FAR_PAGE, "page_no": 0})).json()
    await _attach(client, qid, {**FAR_PAGE, "page_no": 1})

    await client.delete(f"/api/v1/questions/{qid}/figuras/{first['asset_id']}")
    detail = (await client.get(f"/api/v1/questions/{qid}")).json()
    assert [f["page_no"] for f in detail["figures"]] == [1]

  assert len(next(iter(load_decisions(review_paths).values()))["figures"]) == 1


async def test_detach_of_a_missing_figure_is_404(api):
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    assert (
      await client.delete(f"/api/v1/questions/{qid}/figuras/9999")
    ).status_code == (404)


async def test_detach_refuses_a_question_crop(api, tmp_path):
  """O recorte do enunciado não é uma figura e não pode ser desanexado."""
  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    detail = (await client.get(f"/api/v1/questions/{qid}")).json()
    assert detail["pages"]

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    with Session(engine) as session:
      crop = session.scalar(
        select(Asset).where(
          Asset.question_id == qid, Asset.kind == AssetKind.QUESTION_CROP
        )
      )
      crop_id = crop.id
    engine.dispose()

    response = await client.delete(f"/api/v1/questions/{qid}/figuras/{crop_id}")
    assert response.status_code == 404


# --- catálogo de regiões soltas -------------------------------------------


async def test_loose_regions_are_computed_from_the_questions(api):
  """As regiões vêm de `question_pages`, não de nomes de arquivo.

  Assim cada uma tem o retângulo exato e pode ser anexada de fato — listar os
  PNGs exportados daria só o nome, e sem retângulo não há o que anexar.
  """
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    regions = (await client.get(f"/api/v1/documents/{docs[0]['id']}/figuras")).json()

    assert regions
    for region in regions:
      assert len(region["bbox"]) == 4
      assert region["bbox"][3] > region["bbox"][1], "altura positiva"
      assert region["page_no"] >= 0
      assert region["image_url"]


async def test_loose_regions_avoid_the_question_rectangles(api):
  """Uma região solta não pode se sobrepor ao retângulo de uma questão."""
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    regions = (await client.get(f"/api/v1/documents/{docs[0]['id']}/figuras")).json()
    listing = (await client.get("/api/v1/questions")).json()
    detail = (await client.get(f"/api/v1/questions/{listing['items'][0]['id']}")).json()

    page = detail["pages"][0]
    qy0, qy1 = page["bbox"][1], page["bbox"][3]
    for region in [r for r in regions if r["page_no"] == page["page_no"]]:
      ry0, ry1 = region["bbox"][1], region["bbox"][3]
      assert ry1 <= qy0 or ry0 >= qy1, f"regiao {region['name']} invade a questao"


async def test_region_preview_renders(api):
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    regions = (await client.get(f"/api/v1/documents/{docs[0]['id']}/figuras")).json()
    image = await client.get(regions[0]["image_url"])
    assert image.status_code == 200
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")


async def test_region_preview_of_a_degenerate_rect_is_422(api):
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    response = await client.get(
      f"/api/v1/documents/{docs[0]['id']}/regiao?page_no=0&x0=0&y0=0&x1=0&y1=0"
    )
    assert response.status_code == 422


async def test_regions_of_a_missing_document_is_404(api):
  async with api as client:
    assert (await client.get("/api/v1/documents/9999/figuras")).status_code == 404
    assert (await client.get("/api/v1/documents/9999/regiao")).status_code == 404


async def test_a_region_from_the_catalogue_can_be_attached(api):
  """O fluxo completo: escolher no catálogo e anexar com o bbox exato."""
  async with api as client:
    docs = (await client.get("/api/v1/documents")).json()
    regions = (await client.get(f"/api/v1/documents/{docs[0]['id']}/figuras")).json()
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)

    region = regions[0]
    response = await _attach(
      client,
      qid,
      {
        "page_no": region["page_no"],
        "part_index": 0,
        "x0": region["bbox"][0],
        "y0": region["bbox"][1],
        "x1": region["bbox"][2],
        "y1": region["bbox"][3],
      },
    )
    assert response.status_code == 201
    assert response.json()["page_no"] == region["page_no"]


# --- sobrevive ao rebuild --------------------------------------------------


async def test_figures_survive_a_rebuild_without_duplicating(
  api, review_paths, tmp_path, monkeypatch
):
  """A associação é decisão humana: um `--rebuild` não pode perdê-la."""
  import src.etl.load.main as load_main
  from src.etl.load.main import LoadStats, _apply_review_decisions

  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    await _attach(client, qid, {**FAR_PAGE, "page_no": 0})
    await _attach(client, qid, {**FAR_PAGE, "page_no": 1})

  decisions = load_decisions(review_paths)
  monkeypatch.setattr(load_main, "load_decisions", lambda: decisions)

  engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")

  def figures_in_db() -> int:
    with Session(engine) as session:
      return len(
        session.scalars(select(Asset).where(Asset.kind == AssetKind.FIGURE)).all()
      )

  # Simula o rebuild: as figuras somem junto com o banco reconstruído.
  with Session(engine) as session:
    for asset in session.scalars(select(Asset).where(Asset.kind == AssetKind.FIGURE)):
      session.delete(asset)
    session.commit()
  assert figures_in_db() == 0

  stats = LoadStats()
  with Session(engine) as session:
    _apply_review_decisions(session, stats)
    session.commit()
  assert stats.figures_attached == 2
  assert figures_in_db() == 2

  # E um segundo replay não duplica.
  second = LoadStats()
  with Session(engine) as session:
    _apply_review_decisions(session, second)
    session.commit()
  assert figures_in_db() == 2, "o replay substitui, nao acrescenta"
  engine.dispose()


async def test_a_bad_figure_bbox_is_reported_not_fatal(
  api, review_paths, tmp_path, monkeypatch
):
  """Um retângulo impossível registrado não pode derrubar o load."""
  import src.etl.load.main as load_main
  from src.etl.load.main import LoadStats, _apply_review_decisions

  async with api as client:
    listing = (await client.get("/api/v1/questions")).json()
    qid = next(i["id"] for i in listing["items"] if i["number"] == 1)
    await _attach(client, qid)

  decisions = load_decisions(review_paths)
  for entry in decisions.values():
    entry["figures"] = [{**FAR_PAGE, "page_no": 999}]
  monkeypatch.setattr(load_main, "load_decisions", lambda: decisions)

  engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
  stats = LoadStats()
  with Session(engine) as session:
    _apply_review_decisions(session, stats)
    session.commit()
  engine.dispose()

  assert stats.figures_attached == 0
  assert any("figura" in failure for failure in stats.failed_operations)


# --- modelo ---------------------------------------------------------------


def test_figure_is_a_distinct_asset_kind():
  """`FIGURE` existe justamente para não se confundir com o recorte da questão."""
  assert AssetKind.FIGURE.value == "figure"
  assert AssetKind.FIGURE is not AssetKind.QUESTION_CROP


@pytest.mark.parametrize("kind", list(AssetKind))
def test_every_asset_kind_is_accepted_by_the_check_constraint(kind, tmp_path):
  """O CHECK do banco tem que conhecer todos os valores do enum.

  `enum_col` usa `create_constraint=True`, então acrescentar um membro sem
  migration faz o insert falhar em produção e passar nos testes que não tocam o
  banco.
  """
  from src.constants import FormatType, TestType
  from src.models import Document, Exam
  from src.models.enums import DocumentKind, Phase, ReviewStatus

  engine = create_engine(f"sqlite:///{tmp_path / 'kinds.db'}")
  create_schema(engine)
  with Session(engine) as session:
    exam = Exam(test_type=TestType.VESTIBULAR, year=2020, phase=Phase.UNICA)
    session.add(exam)
    session.flush()
    document = Document(
      exam_id=exam.id,
      kind=DocumentKind.PROVA,
      source_column="c",
      local_path="p",
      sha256="s",
    )
    session.add(document)
    session.flush()
    question = Question(
      document_id=document.id,
      exam_id=exam.id,
      number=1,
      format_type=FormatType.OBJECTIVE,
      statement_raw="x",
      page_start=0,
      page_end=0,
      source_sha256="h",
      review_status=ReviewStatus.RAW,
    )
    question.assets.append(
      Asset(kind=kind, relative_path=f"a/{kind.value}.png", page_no=0, sha256="s")
    )
    session.add(question)
    session.commit()
  engine.dispose()
