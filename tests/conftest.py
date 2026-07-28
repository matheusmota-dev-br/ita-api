import pymupdf
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.constants import Discipline, FormatType, TestType
from src.database import get_db
from src.main import app
from src.models import Alternative, Asset, Base, Document, Exam, Question
from src.models.enums import AssetKind, DocumentKind, Phase, ReviewStatus
from src.models.question import QuestionPage


@pytest.fixture
def make_pdf(tmp_path):
  """Gera um PDF de texto simples a partir de uma lista de páginas.

  Os testes de detecção precisam de PDFs reais com camada de texto e
  coordenadas — não dá para simular `page.get_text("dict")` de forma útil.
  """

  def _make(pages: list[str], name: str = "prova.pdf", width=595, height=842):
    doc = pymupdf.open()
    for body in pages:
      page = doc.new_page(width=width, height=height)
      page.insert_text((56, 60), body, fontsize=11)
    path = tmp_path / name
    doc.save(str(path))
    doc.close()
    return path

  return _make


# Réplica do que a migration de FTS5 cria: os testes usam create_all, que não
# conhece tabelas virtuais.
FTS_SQL = [
  """CREATE VIRTUAL TABLE questions_fts USING fts5(
       search_blob, content='questions', content_rowid='id',
       tokenize="unicode61 remove_diacritics 2")""",
  """CREATE TRIGGER questions_fts_ai AFTER INSERT ON questions BEGIN
       INSERT INTO questions_fts(rowid, search_blob) VALUES (new.id, new.search_blob);
     END""",
]


@pytest.fixture
def api(tmp_path, monkeypatch):
  db_path = tmp_path / "test.db"
  media_root = tmp_path / "media"
  (media_root / "crops").mkdir(parents=True)
  # PNG 1x1 válido, para o endpoint de imagem ter o que servir.
  png = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6300010000050001'0d0a2db40000000049454e44ae426082".replace(
      "'", ""
    )
  )
  (media_root / "crops" / "q1.png").write_bytes(png)

  engine = create_engine(f"sqlite:///{db_path}")
  Base.metadata.create_all(engine)
  with engine.connect() as conn:
    for statement in FTS_SQL:
      conn.exec_driver_sql(statement)
    conn.commit()

  with Session(engine) as session:
    exam = Exam(test_type=TestType.VESTIBULAR, year=2025, phase=Phase.PRIMEIRA)
    session.add(exam)
    session.flush()
    document = Document(
      exam_id=exam.id,
      kind=DocumentKind.PROVA,
      source_column="prova_1f",
      local_path="data/x.pdf",
      sha256="abc",
    )
    session.add(document)
    session.flush()

    for number, discipline, blob in [
      (1, Discipline.MATEMATICA, "Seja z um número complexo de módulo unitário"),
      (2, Discipline.FISICA, "Calcule a aceleração média do bloco"),
      (3, Discipline.MATEMATICA, "Determine a função afim que passa pelos pontos"),
    ]:
      question = Question(
        document_id=document.id,
        exam_id=exam.id,
        number=number,
        format_type=FormatType.OBJECTIVE,
        discipline=discipline,
        statement_raw=blob,
        page_start=0,
        page_end=0,
        source_sha256=f"s{number}",
        review_status=ReviewStatus.RAW,
        search_blob=blob,
      )
      session.add(question)
      session.flush()
      session.add(
        QuestionPage(
          question_id=question.id,
          part_index=0,
          page_no=0,
          x0=0,
          y0=0,
          x1=595,
          y1=200,
        )
      )
      # Só a questão 1 tem imagem e gabarito conhecido; as outras existem para
      # que os filtros tenham o que excluir.
      if number == 1:
        session.add(
          Asset(
            kind=AssetKind.QUESTION_CROP,
            question_id=question.id,
            relative_path="crops/q1.png",
            page_no=0,
            sha256="img1",
          )
        )
      for index, letter in enumerate("ABCDE"):
        session.add(
          Alternative(
            question_id=question.id,
            letter=letter,
            order_index=index,
            is_correct=(letter == "C") if number == 1 else None,
            review_status=ReviewStatus.RAW,
          )
        )
    session.commit()

  monkeypatch.setattr("src.routers.questions.settings.media_root", media_root)

  from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

  async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
  maker = async_sessionmaker(async_engine, expire_on_commit=False)

  async def override():
    # Espelha src.database.get_db, que faz commit ao final — sem isso o POST
    # de revisão nao persistiria e o teste passaria por engano.
    async with maker() as session:
      yield session
      await session.commit()

  app.dependency_overrides[get_db] = override
  yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
  app.dependency_overrides.clear()
