"""Confere se o estado atual bate com o esperado.

Documentar os passos não prova reprodutibilidade — prova que alguém escreveu os
passos. Este comando calcula um conjunto de contagens a partir do banco e as
compara com um baseline versionado. Quem rodar o pipeline do zero e obtiver o
mesmo fingerprint chegou ao mesmo resultado; quem obtiver outro vê exatamente
qual contagem divergiu.

O baseline fica em `data/review/baseline.json`, versionado junto das decisões,
porque é a mesma categoria de coisa: uma afirmação humana sobre o que é o
resultado correto.
"""

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src.config import PROJECT_ROOT, settings
from src.etl.provenance import capture, fingerprint
from src.models import (
  Alternative,
  AnswerKeyEntry,
  Asset,
  Document,
  Exam,
  Question,
)
from src.models.enums import ReviewStatus

BASELINE_PATH = PROJECT_ROOT / "data" / "review" / "baseline.json"


def measure() -> dict:
  """Contagens que descrevem o resultado do pipeline.

  Escolhidas para pegar regressões reais: perder questões, perder texto, perder
  o casamento do gabarito ou a atribuição de matéria. Nenhuma depende de
  timestamp ou de id autoincremento, que mudariam a cada execução sem que nada
  de fato tivesse mudado.
  """
  engine = create_engine(settings.database_url_sync, future=True)
  with Session(engine) as session:
    scalar = lambda stmt: session.scalar(stmt) or 0  # noqa: E731

    counts = {
      "exams": scalar(select(func.count()).select_from(Exam)),
      "documents": scalar(select(func.count()).select_from(Document)),
      "questions": scalar(select(func.count()).select_from(Question)),
      "questions_with_text": scalar(
        select(func.count())
        .select_from(Question)
        .where(func.length(func.trim(Question.statement_raw)) > 0)
      ),
      "questions_with_discipline": scalar(
        select(func.count())
        .select_from(Question)
        .where(Question.discipline.is_not(None))
      ),
      "questions_with_image": scalar(
        select(func.count(func.distinct(Asset.question_id)))
      ),
      "questions_with_answer": scalar(
        select(func.count(func.distinct(Alternative.question_id))).where(
          Alternative.is_correct.is_(True)
        )
      ),
      "answer_key_entries": scalar(select(func.count()).select_from(AnswerKeyEntry)),
      # As alternativas nascem vazias e o parser determinístico preenche o que
      # consegue. Contar isto transforma "perdi o texto das alternativas" numa
      # divergência visível em vez de uma degradação silenciosa.
      "alternatives_with_text": scalar(
        select(func.count())
        .select_from(Alternative)
        .where(
          func.length(func.trim(func.coalesce(Alternative.text_raw, ""))) > 0,
        )
      ),
      "human_verified": scalar(
        select(func.count())
        .select_from(Question)
        .where(Question.review_status == ReviewStatus.HUMAN_VERIFIED)
      ),
    }

    by_discipline = {
      (row[0].value if row[0] else "sem_materia"): row[1]
      for row in session.execute(
        select(Question.discipline, func.count()).group_by(Question.discipline)
      )
    }
    by_year = {
      str(row[0]): row[1]
      for row in session.execute(
        select(Exam.year, func.count())
        .join(Question, Question.exam_id == Exam.id)
        .group_by(Exam.year)
      )
    }
  engine.dispose()

  return {
    "counts": counts,
    "by_discipline": dict(sorted(by_discipline.items())),
    "by_year": dict(sorted(by_year.items())),
  }


def _diff(expected: dict, actual: dict, path: str = "") -> list[str]:
  out: list[str] = []
  for key in sorted(set(expected) | set(actual)):
    where = f"{path}.{key}" if path else key
    a, b = expected.get(key), actual.get(key)
    if isinstance(a, dict) or isinstance(b, dict):
      out += _diff(a or {}, b or {}, where)
    elif a != b:
      delta = f" ({b - a:+})" if isinstance(a, int) and isinstance(b, int) else ""
      out.append(f"{where}: esperado {a}, obtido {b}{delta}")
  return out


def cmd_snapshot(path: Path) -> int:
  state = measure()
  payload = {
    "fingerprint": fingerprint(state),
    "environment": capture(),
    **state,
  }
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(
    json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
  )
  print(f"baseline gravado em {path}")
  print(f"fingerprint: {payload['fingerprint']}")
  return 0


def cmd_verify(path: Path) -> int:
  if not path.exists():
    print(
      f"nenhum baseline em {path}; rode `task verify -- --snapshot`", file=sys.stderr
    )
    return 1

  expected = json.loads(path.read_text(encoding="utf-8"))
  actual = measure()
  actual_fp = fingerprint(actual)

  print(f"esperado : {expected['fingerprint']}")
  print(f"obtido   : {actual_fp}")

  if actual_fp == expected["fingerprint"]:
    print("\nOK — o estado bate com o baseline.")
    return 0

  print("\nDIVERGENCIA:")
  for line in _diff(
    {k: expected[k] for k in ("counts", "by_discipline", "by_year") if k in expected},
    actual,
  ):
    print(f"  {line}")

  env_now, env_then = capture(), expected.get("environment", {})
  env_diff = _diff(env_then, env_now)
  if env_diff:
    print("\nO ambiente também mudou — provável causa:")
    for line in env_diff:
      print(f"  {line}")
  return 1


def main() -> int:
  parser = argparse.ArgumentParser(
    description="Confere o resultado do pipeline contra um baseline versionado."
  )
  parser.add_argument(
    "--snapshot",
    action="store_true",
    help="grava o estado atual como o novo baseline",
  )
  parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
  args = parser.parse_args()
  return cmd_snapshot(args.baseline) if args.snapshot else cmd_verify(args.baseline)


if __name__ == "__main__":
  sys.exit(main())
