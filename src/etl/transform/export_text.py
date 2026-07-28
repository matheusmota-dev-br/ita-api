from pathlib import Path

from src.etl.transform.paths import get_question_texts_dir


def save_question_text_files(
  inside_by_question: dict[int, str],
  outside_by_question: dict[int, str],
  out_dir: Path | None = None,
) -> Path:
  out_dir = out_dir or get_question_texts_dir()
  out_dir.mkdir(parents=True, exist_ok=True)
  for num in inside_by_question:
    (out_dir / f"questao_{num:02d}_dentro.txt").write_text(
      inside_by_question[num], encoding="utf-8"
    )
    if outside_by_question.get(num):
      (out_dir / f"questao_{num:02d}_fora.txt").write_text(
        outside_by_question[num], encoding="utf-8"
      )
  return out_dir
