"""Carrega os artefatos do transform no SQLite."""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.config import PROJECT_ROOT, ensure_sqlite_dir, settings
from src.constants import Discipline
from src.etl.load.decisions import (
  OP_CREATE,
  OP_DELETE,
  load_decisions,
  load_operations,
)
from src.etl.load.manual import build_manual_question, render_manual_crop
from src.etl.load.reader import LoadedDocument, iter_processed
from src.etl.load.upserts import (
  ensure_alternatives,
  replace_question_assets,
  replace_question_pages,
  upsert_answer_key_entry,
  upsert_document,
  upsert_exam,
  upsert_question,
)
from src.etl.transform.crop import CropError
from src.etl.transform.paths import ENRICHED_SUBDIR
from src.models import Document, Question, Topic
from src.models.enums import DisciplineSource, ReviewStatus

# Prefixo dos caminhos de imagem gravados em `assets`, relativo a MEDIA_ROOT.
MEDIA_PREFIX_ROOT = "processed"


@dataclass
class LoadStats:
  exams: int = 0
  documents: int = 0
  questions: int = 0
  questions_updated: int = 0
  alternatives_marked: int = 0
  enriched: int = 0
  disciplines_from_llm: int = 0
  answers: int = 0
  disciplines_from_answer_key: int = 0
  decisions_applied: int = 0
  orphan_decisions: int = 0
  questions_created: int = 0
  questions_deleted: int = 0
  questions_renumbered: int = 0
  failed_operations: list[str] = field(default_factory=list)
  skipped: list[str] = field(default_factory=list)

  def summary(self) -> str:
    return (
      f"{self.documents} documentos, {self.questions} questoes "
      f"({self.questions_updated} escritas), {self.answers} respostas de gabarito, "
      f"{self.alternatives_marked} alternativas corretas marcadas, "
      f"{self.enriched} enriquecidas pelo LLM"
    )


def _read_statement(directory: Path, entry: dict) -> str:
  relative = entry.get("text_inside")
  if not relative:
    return ""
  path = directory / relative
  return path.read_text(encoding="utf-8") if path.exists() else ""


def _build_search_blob(question: Question) -> str:
  parts = [question.statement_markdown or question.statement_raw or ""]
  parts.extend(alt.text_markdown or alt.text_raw or "" for alt in question.alternatives)
  return "\n".join(p for p in parts if p.strip())


def _read_enriched(directory: Path, number: int) -> dict | None:
  """Saída do LLM para uma questão, se o estágio de enrich já rodou."""
  path = directory / ENRICHED_SUBDIR / f"questao_{number:02d}.json"
  if not path.exists():
    return None
  return json.loads(path.read_text(encoding="utf-8"))


def _apply_enrichment(
  session: Session, question: Question, payload: dict, stats: LoadStats
) -> None:
  """Escreve os campos normalizados sem tocar nos campos `*_raw`.

  A extração heurística continua sendo a fonte auditável; o LLM só preenche as
  colunas Markdown. Nada aqui sobrescreve uma questão já revisada por humano.
  """
  if question.review_status is ReviewStatus.HUMAN_VERIFIED:
    return

  question.statement_markdown = payload.get("statement_markdown")
  question.has_figure = payload.get("has_figure")
  question.review_status = ReviewStatus.LLM_GENERATED

  # A matéria detectada só entra se ainda não houver uma fonte melhor:
  # cabeçalho de seção e nome de arquivo são mais confiáveis que o LLM.
  detected = payload.get("detected_discipline")
  if question.discipline is None and detected and detected != "indeterminado":
    question.discipline = Discipline(detected)
    question.discipline_source = DisciplineSource.LLM
    question.discipline_confidence = 0.7
    stats.disciplines_from_llm += 1

  by_letter = {alt.letter: alt for alt in question.alternatives}
  for item in payload.get("alternatives", []):
    alternative = by_letter.get(item["letter"])
    if alternative is None or alternative.review_status is ReviewStatus.HUMAN_VERIFIED:
      continue
    alternative.text_markdown = item.get("text_markdown")
    alternative.is_figure_only = bool(item.get("is_figure_only"))
    alternative.review_status = ReviewStatus.LLM_GENERATED

  _apply_topics(session, question, payload.get("detected_topics", []))
  stats.enriched += 1


def _apply_topics(session: Session, question: Question, slugs: list[str]) -> None:
  """Upsert de assuntos por slug, para que grafias variantes convirjam."""
  existing = {topic.slug for topic in question.topics}
  for slug in slugs:
    normalized = slug.strip().lower().replace(" ", "-")
    if not normalized or normalized in existing:
      continue
    topic = session.scalars(select(Topic).where(Topic.slug == normalized)).one_or_none()
    if topic is None:
      topic = Topic(
        slug=normalized,
        label=normalized.replace("-", " ").capitalize(),
        discipline=question.discipline,
      )
      session.add(topic)
      session.flush()
    question.topics.append(topic)
    existing.add(normalized)


def _load_questions(session: Session, doc: LoadedDocument, stats: LoadStats) -> None:
  manifest = doc.manifest
  if manifest is None:
    return

  exam = upsert_exam(session, manifest)
  document = upsert_document(session, exam, manifest)
  media_prefix = f"{MEDIA_PREFIX_ROOT}/{doc.directory.name}"
  # A coluna existia e nunca era preenchida. A interface precisa dela para
  # saber onde gravar o recorte de uma questão criada à mão, e derivar o slug
  # de novo a partir do nome do PDF exigiria o `_index.json` do extract.
  document.processed_dir = media_prefix

  for entry in manifest.get("questions", []):
    statement = _read_statement(doc.directory, entry)
    question, changed = upsert_question(session, exam, document, entry, statement)
    stats.questions += 1

    if changed:
      stats.questions_updated += 1
      replace_question_pages(session, question, entry.get("parts", []))
      replace_question_assets(session, question, entry.get("parts", []), media_prefix)
      ensure_alternatives(session, question)

    enriched = _read_enriched(doc.directory, entry["number"])
    if enriched is not None:
      _apply_enrichment(session, question, enriched, stats)


def _load_answer_key(session: Session, doc: LoadedDocument, stats: LoadStats) -> None:
  payload = doc.answer_key
  if payload is None:
    return

  exam = upsert_exam(session, payload)
  document = upsert_document(session, exam, {**payload, "kind": "gabarito"})

  for answer in payload.get("answers", []):
    upsert_answer_key_entry(session, exam, document, answer)
    stats.answers += 1


def _apply_answer_keys(session: Session, stats: LoadStats) -> None:
  """Projeta o gabarito nas alternativas e completa matérias faltantes.

  O casamento tenta primeiro (prova, matéria, número), que é o que desambigua a
  era 2008–2018, onde cada matéria recomeça a numeração em 1. Só depois cai
  para (prova, número), que basta para a 1ª fase moderna, de numeração global.
  """
  from src.models import AnswerKeyEntry

  entries = session.scalars(select(AnswerKeyEntry)).all()
  for entry in entries:
    query = select(Question).where(
      Question.exam_id == entry.exam_id, Question.number == entry.question_number
    )
    if entry.discipline is not None:
      candidates = session.scalars(
        query.where(Question.discipline == entry.discipline)
      ).all()
      if not candidates:
        # A questão pode não ter matéria ainda: o gabarito então vira a fonte.
        candidates = session.scalars(query.where(Question.discipline.is_(None))).all()
        for question in candidates:
          question.discipline = entry.discipline
          question.discipline_source = DisciplineSource.ANSWER_KEY
          question.discipline_confidence = 0.9
          stats.disciplines_from_answer_key += 1
    else:
      candidates = session.scalars(query).all()

    if len(candidates) != 1 or entry.correct_letter is None:
      continue

    question = candidates[0]
    for alternative in question.alternatives:
      should_be = alternative.letter == entry.correct_letter
      if alternative.review_status is ReviewStatus.HUMAN_VERIFIED:
        continue
      if alternative.is_correct != should_be:
        alternative.is_correct = should_be
    stats.alternatives_marked += 1
  session.flush()


def _apply_decision_bbox(question: Question, bbox: dict | None) -> None:
  """Reaplica um retângulo corrigido à mão.

  O PNG em disco já foi regerado no momento da correção e o transform não o
  sobrescreve (o manifest não mudou), então aqui basta realinhar as linhas do
  banco com o que o revisor decidiu.
  """
  if not bbox:
    return
  page_row = next(
    (p for p in question.pages if p.part_index == bbox.get("part_index", 0)), None
  )
  if page_row is None:
    return
  page_row.page_no = bbox["page_no"]
  page_row.x0, page_row.y0 = bbox["x0"], bbox["y0"]
  page_row.x1, page_row.y1 = bbox["x1"], bbox["y1"]
  question.page_start = min(p.page_no for p in question.pages)
  question.page_end = max(p.page_no for p in question.pages)


def _replay_operations(session: Session, stats: LoadStats) -> None:
  """Reexecuta criar / excluir / renumerar, na ordem em que foram registradas.

  Roda **antes** das decisões por questão, porque define quais questões existem
  e sob que número — e a chave de uma decisão é justamente (caminho, número).
  Inverter a ordem faria uma correção de texto procurar uma questão que a
  renumeração ainda não tinha movido.

  A ordem entre operações também importa: renumerar 5→6 e depois 6→7 não é o
  mesmo que o contrário, por isso a lista é cronológica e não um mapa.
  """
  operations = load_operations()
  if not operations:
    return

  by_path: dict[str, Document] = {
    document.local_path: document for document in session.scalars(select(Document))
  }
  for op in operations:
    document = by_path.get(op["local_path"])
    if document is None:
      stats.failed_operations.append(
        f"{op['op']}: documento ausente ({op['local_path']})"
      )
      continue
    try:
      _replay_one(session, document, op, stats)
    except (CropError, OSError, IntegrityError) as exc:
      session.rollback()
      stats.failed_operations.append(
        f"{op['op']} {op['local_path']}#{op['number']}: {exc}"
      )
  session.flush()


def _find(session: Session, document: Document, number: int) -> Question | None:
  return session.scalars(
    select(Question).where(
      Question.document_id == document.id, Question.number == number
    )
  ).one_or_none()


def _replay_one(
  session: Session, document: Document, op: dict, stats: LoadStats
) -> None:
  number = op["number"]
  existing = _find(session, document, number)

  if op["op"] == OP_DELETE:
    if existing is not None:
      session.delete(existing)
      stats.questions_deleted += 1
    return

  if op["op"] == OP_CREATE:
    if existing is not None:
      # O transform passou a detectar a questão que antes foi criada à mão.
      # A detecção vence: é reproduzível, o retângulo manual não precisa mais.
      return
    pdf_path = Path(document.local_path)
    if not pdf_path.is_absolute():
      pdf_path = PROJECT_ROOT / pdf_path
    crop, relative = render_manual_crop(pdf_path, document, number, op["bbox"])
    session.add(build_manual_question(document, number, op["bbox"], crop, relative))
    session.flush()
    stats.questions_created += 1
    return

  # renumerar
  if existing is None:
    stats.failed_operations.append(f"renumber: {document.local_path}#{number} ausente")
    return
  target = op["to"]
  occupant = _find(session, document, target)
  if occupant is not None:
    # UNIQUE(document_id, number) recusaria o estado intermediário em que as
    # duas têm o mesmo número, daí o desvio por um número negativo — e um flush
    # por etapa, porque um flush único emitiria os UPDATE numa ordem que já
    # passaria pelo estado proibido.
    occupant.number = -abs(number)
    session.flush()
    existing.number = target
    session.flush()
    occupant.number = number
  else:
    existing.number = target
  session.flush()
  stats.questions_renumbered += 1


def _apply_review_decisions(session: Session, stats: LoadStats) -> None:
  """Reaplica a revisão humana registrada em data/review/decisions.json.

  Roda por último, depois do gabarito e do enrich, para que a decisão humana
  seja a palavra final. É o que faz a auditoria sobreviver a um `--rebuild`,
  que apaga o arquivo do banco inteiro.
  """
  decisions = load_decisions()
  if not decisions:
    return

  by_document: dict[str, Document] = {
    document.local_path: document for document in session.scalars(select(Document))
  }
  for decision in decisions.values():
    document = by_document.get(decision["local_path"])
    if document is None:
      stats.orphan_decisions += 1
      continue
    question = session.scalars(
      select(Question).where(
        Question.document_id == document.id, Question.number == decision["number"]
      )
    ).one_or_none()
    if question is None:
      stats.orphan_decisions += 1
      continue
    if decision.get("status"):
      question.review_status = ReviewStatus(decision["status"])
    if decision.get("needs_llm") is not None:
      question.needs_llm = decision["needs_llm"]
    if decision.get("statement_markdown"):
      question.statement_markdown = decision["statement_markdown"]
      question.review_status = ReviewStatus.HUMAN_VERIFIED
    if decision.get("alternatives"):
      by_letter = {a.letter: a for a in question.alternatives}
      for letter, text in decision["alternatives"].items():
        alternative = by_letter.get(letter.upper())
        if alternative is not None:
          alternative.text_markdown = text or None
          alternative.review_status = ReviewStatus.HUMAN_VERIFIED
    _apply_decision_bbox(question, decision.get("bbox"))
    stats.decisions_applied += 1
  session.flush()


def _refresh_search_blobs(session: Session) -> None:
  for question in session.scalars(select(Question)).all():
    blob = _build_search_blob(question)
    if question.search_blob != blob:
      question.search_blob = blob
  session.flush()


def _finalise(engine) -> None:
  """Deixa o .db como artefato único, autocontido e read-only-friendly.

  Roda fora da sessão e em AUTOCOMMIT: `PRAGMA journal_mode` e `VACUUM` não
  podem executar dentro de uma transação. WAL precisa escrever os sidecars
  -wal/-shm, e como a API monta o arquivo somente-leitura, o modo tem que
  voltar para DELETE antes de publicar.
  """
  with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
    connection.exec_driver_sql(
      "INSERT INTO questions_fts(questions_fts) VALUES('optimize')"
    )
    connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.exec_driver_sql("PRAGMA journal_mode=DELETE")
    connection.exec_driver_sql("PRAGMA optimize")
    connection.exec_driver_sql("VACUUM")


def run_load(*, processed_root: Path | None = None, verbose: bool = True) -> LoadStats:
  ensure_sqlite_dir()
  engine = create_engine(settings.database_url_sync, future=True)
  stats = LoadStats()

  documents = iter_processed(processed_root)
  with Session(engine) as session:
    session.connection().exec_driver_sql("PRAGMA foreign_keys=ON")

    for doc in documents:
      stats.documents += 1
      if doc.manifest is not None:
        _load_questions(session, doc, stats)
      if doc.answer_key is not None:
        _load_answer_key(session, doc, stats)
      if verbose:
        print(f"  {doc.slug}")

    _apply_answer_keys(session, stats)
    # Antes das decisões por questão: define quais questões existem e sob que
    # número, e a chave de uma decisão é (caminho, número).
    _replay_operations(session, stats)
    _apply_review_decisions(session, stats)
    _refresh_search_blobs(session)
    session.commit()

  _finalise(engine)
  engine.dispose()
  return stats


def _rebuild() -> None:
  """Apaga o banco e reaplica as migrations — o .db é artefato descartável."""
  path = settings.sqlite_path
  if path is not None and path.exists():
    path.unlink()
  ensure_sqlite_dir()

  from alembic.config import Config

  from alembic import command

  config = Config("alembic.ini")
  command.upgrade(config, "head")


def main() -> int:
  parser = argparse.ArgumentParser(description="Carrega os artefatos no SQLite.")
  parser.add_argument(
    "--rebuild",
    action="store_true",
    help="apaga o banco e reaplica as migrations antes de carregar",
  )
  parser.add_argument("--quiet", action="store_true")
  args = parser.parse_args()

  if args.rebuild:
    print("recriando o banco...")
    _rebuild()

  stats = run_load(verbose=not args.quiet)
  print(f"\n{stats.summary()}")
  if stats.disciplines_from_answer_key:
    print(f"materias recuperadas do gabarito: {stats.disciplines_from_answer_key}")
  if stats.decisions_applied:
    print(f"decisoes de revisao reaplicadas: {stats.decisions_applied}")
  if stats.orphan_decisions:
    print(f"decisoes sem questao correspondente: {stats.orphan_decisions}")
  structural = (
    stats.questions_created,
    stats.questions_deleted,
    stats.questions_renumbered,
  )
  if any(structural):
    print(
      "operacoes manuais reaplicadas: "
      f"{structural[0]} criadas, {structural[1]} excluidas, "
      f"{structural[2]} renumeradas"
    )
  # Falhar em silêncio aqui reintroduziria a questão excluída ou perderia a
  # criada, e o `task verify` acusaria a divergência sem dizer a causa.
  for failure in stats.failed_operations:
    print(f"  operacao NAO aplicada: {failure}", file=sys.stderr)
  return 0


if __name__ == "__main__":
  sys.exit(main())
