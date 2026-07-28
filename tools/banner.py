"""Gera o banner do README a partir do baseline versionado.

Deterministico por construcao: a unica entrada e `data/review/baseline.json`, o
mesmo arquivo que o `task verify` compara. Nada de random, nada de data/hora,
nada de ler o `.db` (que e gitignored) — dois clones do repo produzem SVGs
byte a byte identicos, e o `git diff` do banner so muda quando o corpus muda.

  PYTHONPATH=. pipenv run python -m tools.banner        # regrava os dois temas
  PYTHONPATH=. pipenv run python -m tools.banner --check # falha se estiver stale

Cada questao do corpus e um quadradinho. As torres sao os anos; a barra de
baixo e a divisao por materia.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "data" / "review" / "baseline.json"
OUT_DIR = ROOT / "docs"

# Geometria, em unidades do viewBox.
W, H = 1200, 420
PAD = 56
TOWER_COLS = 8  # quadradinhos por linha de uma torre
CELL = 6.0  # passo da grade
DOT = 4.4  # lado do quadradinho (CELL - DOT = respiro de 1.6)
BASELINE_Y = 306.0  # onde as torres se apoiam
BAR_Y, BAR_H = 350.0, 14.0

# Paleta: azul sequencial para as torres (uma serie so, sem legenda), e os cinco
# primeiros slots categoricos para a barra de materias — nessa ordem os pares
# adjacentes passam os limiares de CVD, e amarelo nunca encosta em laranja.
# Passos documentados, nao interpolados: em light a torre nao clareia alem do
# passo 250 e em dark nao escurece alem do 500, senao o topo some no fundo.
THEMES = {
  "dark": {
    "surface": "#1a1a19",
    "ink": "#ffffff",
    "ink_soft": "#c3c2b7",
    "ink_mute": "#7d7c73",
    "rule": "#33332f",
    "ramp": [
      "#256abf",
      "#2a78d6",
      "#3987e5",
      "#5598e7",
      "#6da7ec",
      "#86b6ef",
      "#9ec5f4",
      "#b7d3f6",
      "#cde2fb",
    ],
    "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"],
  },
  "light": {
    "surface": "#fcfcfb",
    "ink": "#0b0b0b",
    "ink_soft": "#52514e",
    "ink_mute": "#78766f",
    "rule": "#e6e5e0",
    "ramp": [
      "#0d366b",
      "#104281",
      "#184f95",
      "#1c5cab",
      "#256abf",
      "#2a78d6",
      "#3987e5",
      "#5598e7",
      "#86b6ef",
    ],
    "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"],
  },
}

SANS = (
  "ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
)
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"

DISCIPLINE_LABEL = {
  "matematica": "matemática",
  "fisica": "física",
  "quimica": "química",
  "portugues": "português",
  "ingles": "inglês",
  "computacao": "computação",
  "biologia": "biologia",
}


def esc(text: str) -> str:
  return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def grupos(n: int) -> str:
  """1479 -> '1.479'. `f'{n:,}'` depende de locale; isto nao."""
  return f"{n:,}".replace(",", ".")


def curto(caminho: Path) -> str:
  """Caminho relativo à raiz quando dá; absoluto quando `--out-dir` sai dela."""
  try:
    return str(caminho.relative_to(ROOT))
  except ValueError:
    return str(caminho)


def pct(parte: int, total: int) -> str:
  """Arredonda pra baixo: '100%' so quando e 100% mesmo."""
  if not total:
    return "—"
  return f"{math.floor(parte * 100 / total)}%"


def largura_texto(text: str, size: float, weight: str = "normal") -> float:
  """Largura aproximada de um texto, para decidir se um rotulo cabe.

  Nao ha como medir fonte de sistema aqui, entao vale a razao media de avanco
  de uma humanista: 0.52em, 0.58em em negrito. Chuta largo de proposito — o
  erro aceitavel e esconder um rotulo que caberia, nunca sobrepor dois.
  """
  ratio = 0.58 if weight == "bold" else 0.52
  # Diacriticos nao ocupam avanco proprio.
  bruto = len(unicodedata.normalize("NFD", text)) - sum(
    1 for c in unicodedata.normalize("NFD", text) if unicodedata.combining(c)
  )
  return bruto * size * ratio


def texto(
  x: float,
  y: float,
  s: str,
  *,
  size: float,
  fill: str,
  family: str = SANS,
  weight: str = "normal",
  anchor: str = "start",
  spacing: float | None = None,
  opacity: float | None = None,
) -> str:
  attrs = [
    f'x="{x:g}"',
    f'y="{y:g}"',
    f'font-family="{family}"',
    f'font-size="{size:g}"',
    f'fill="{fill}"',
  ]
  if weight != "normal":
    attrs.append(f'font-weight="{weight}"')
  if anchor != "start":
    attrs.append(f'text-anchor="{anchor}"')
  if spacing is not None:
    attrs.append(f'letter-spacing="{spacing:g}"')
  if opacity is not None:
    attrs.append(f'opacity="{opacity:g}"')
  return f"<text {' '.join(attrs)}>{esc(s)}</text>"


def torres(por_ano: dict[str, int], t: dict) -> list[str]:
  """Uma torre por ano; um quadradinho por questao, preenchendo de baixo pra cima.

  A cor vem da altura da linha, nao do valor — as torres compartilham a mesma
  rampa, entao o topo de uma alcanca a mesma cor que o topo de outra.

  As 2730 questoes saem como um `<path>` por passo da rampa, e nao como 2730
  `<rect>`: mesmo desenho, 224 KB a menos, e sem depender de `<use>`/`<style>`
  dentro do SVG, que o sanitizador do GitHub pode remover.
  """
  anos = sorted(por_ano)
  larg_torre = TOWER_COLS * CELL
  vao = (W - 2 * PAD - len(anos) * larg_torre) / (len(anos) - 1)
  linhas_max = max(math.ceil(v / TOWER_COLS) for v in por_ano.values())
  pico = max(por_ano.values())
  vale = min(por_ano.values())

  trilhas: list[list[str]] = [[] for _ in t["ramp"]]
  rotulos: list[str] = []
  for i, ano in enumerate(anos):
    n = por_ano[ano]
    x0 = PAD + i * (larg_torre + vao)
    linhas = math.ceil(n / TOWER_COLS)
    for idx in range(n):
      linha, col = divmod(idx, TOWER_COLS)
      nesta = min(TOWER_COLS, n - linha * TOWER_COLS)
      # A linha incompleta (sempre a de cima) fica centrada na torre.
      recuo = (TOWER_COLS - nesta) * CELL / 2
      x = x0 + recuo + col * CELL
      y = BASELINE_Y - (linha + 1) * CELL
      passo = round(linha / max(linhas_max - 1, 1) * (len(t["ramp"]) - 1))
      trilhas[passo].append(f"M{x:.1f} {y:.1f}h{DOT:g}v{DOT:g}h-{DOT:g}z")
    # Rotulo direto so nos extremos: pico, vale e as duas pontas da serie.
    if n in (pico, vale) or i in (0, len(anos) - 1):
      rotulos.append(
        texto(
          x0 + larg_torre / 2,
          BASELINE_Y - linhas * CELL - 7,
          str(n),
          size=9.5,
          fill=t["ink_soft"],
          family=MONO,
          anchor="middle",
        )
      )
    rotulos.append(
      texto(
        x0 + larg_torre / 2,
        BASELINE_Y + 15,
        ano,
        size=9.5,
        fill=t["ink_mute"],
        family=MONO,
        anchor="middle",
      )
    )

  out = [
    f'<path fill="{cor}" d="{"".join(d)}"/>' for cor, d in zip(t["ramp"], trilhas) if d
  ]
  return out + rotulos


def barra_materias(por_materia: dict[str, int], t: dict) -> list[str]:
  """Barra empilhada de materias, da maior para a menor, com rotulo direto."""
  itens = sorted(por_materia.items(), key=lambda kv: (-kv[1], kv[0]))
  total = sum(v for _, v in itens) or 1
  larg_total = W - 2 * PAD
  # 2px de fundo entre segmentos: o respiro, nao uma borda.
  util = larg_total - 2 * (len(itens) - 1)

  out: list[str] = []
  x = float(PAD)
  for i, (chave, n) in enumerate(itens):
    cor = t["series"][i % len(t["series"])]
    larg = util * n / total
    out.append(
      f'<rect x="{x:.2f}" y="{BAR_Y:g}" width="{larg:.2f}" height="{BAR_H:g}"'
      f' rx="2" fill="{cor}"/>'
    )
    rotulo = f"{DISCIPLINE_LABEL.get(chave, chave)} {grupos(n)}"
    if largura_texto(rotulo, 11) + 13 <= larg:
      ys = BAR_Y + BAR_H + 15
      out.append(
        f'<rect x="{x:.2f}" y="{ys - 7.5:.2f}" width="7.5" height="7.5"'
        f' rx="1.6" fill="{cor}"/>'
      )
      out.append(texto(x + 13, ys, rotulo, size=11, fill=t["ink_soft"]))
    x += larg + 2
  return out


def svg(dados: dict, tema: str) -> str:
  t = THEMES[tema]
  c = dados["counts"]
  anos = sorted(dados["by_year"])
  fp = dados.get("fingerprint", "")[:8]

  partes = [
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"'
    f' width="{W}" height="{H}" role="img"'
    f' aria-label="ita-api: {grupos(c["questions"])} questões do ITA,'
    f' {anos[0]}–{anos[-1]}, extraídas de {c["documents"]} PDFs oficiais.">',
    f'<rect width="{W}" height="{H}" fill="{t["surface"]}"/>',
    texto(PAD, 82, "ita-api", size=46, fill=t["ink"], weight="bold", spacing=-1.2),
    texto(
      PAD,
      108,
      "Banco de questões dos vestibulares e da pós do ITA, "
      "extraídas dos PDFs oficiais e servidas por uma API.",
      size=14,
      fill=t["ink_soft"],
    ),
    texto(
      PAD,
      132,
      " · ".join(
        [
          f"{grupos(c['documents'])} PDFs",
          f"{grupos(c['exams'])} provas",
          f"{grupos(c['questions'])} questões",
          f"{grupos(c['alternatives_with_text'])} alternativas",
        ]
      ),
      size=11.5,
      fill=t["ink_mute"],
      family=MONO,
    ),
    texto(
      W - PAD,
      82,
      f"{anos[0]}–{anos[-1]}",
      size=13,
      fill=t["ink_mute"],
      family=MONO,
      anchor="end",
    ),
    texto(
      W - PAD,
      132,
      " · ".join(
        [
          f"{pct(c['questions_with_image'], c['questions'])} com recorte",
          f"{pct(c['questions_with_discipline'], c['questions'])} com matéria",
          f"{pct(c['questions_with_answer'], c['questions'])} com gabarito",
        ]
      ),
      size=11.5,
      fill=t["ink_mute"],
      family=MONO,
      anchor="end",
    ),
    f'<line x1="{PAD}" y1="{BASELINE_Y + 2.5:g}" x2="{W - PAD}"'
    f' y2="{BASELINE_Y + 2.5:g}" stroke="{t["rule"]}" stroke-width="1"/>',
  ]
  partes += torres(dados["by_year"], t)
  partes += barra_materias(dados["by_discipline"], t)
  partes.append(
    texto(
      PAD,
      402,
      "cada quadrado é uma questão · torres por ano · barra por matéria",
      size=10,
      fill=t["ink_mute"],
    )
  )
  partes.append(
    texto(
      W - PAD,
      402,
      f"tools/banner.py · baseline {fp}",
      size=10,
      fill=t["ink_mute"],
      family=MONO,
      anchor="end",
    )
  )
  partes.append("</svg>")
  return "\n".join(partes) + "\n"


def main(argv: list[str] | None = None) -> int:
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument(
    "--check",
    action="store_true",
    help="não escreve; sai com 1 se o SVG em disco divergir do baseline",
  )
  ap.add_argument("--baseline", type=Path, default=BASELINE)
  ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
  args = ap.parse_args(argv)

  if not args.baseline.exists():
    print(
      f"baseline ausente: {args.baseline}\n"
      "rode `task verify -- --snapshot` para gravá-lo.",
      file=sys.stderr,
    )
    return 2
  dados = json.loads(args.baseline.read_text(encoding="utf-8"))
  for campo in ("by_year", "by_discipline", "counts"):
    if not dados.get(campo):
      print(f"baseline sem `{campo}`: nada a desenhar.", file=sys.stderr)
      return 2

  args.out_dir.mkdir(parents=True, exist_ok=True)
  stale = []
  for tema in THEMES:
    destino = args.out_dir / f"banner-{tema}.svg"
    conteudo = svg(dados, tema)
    atual = destino.read_text(encoding="utf-8") if destino.exists() else None
    if args.check:
      if atual != conteudo:
        stale.append(destino)
      continue
    if atual != conteudo:
      destino.write_text(conteudo, encoding="utf-8")
      print(f"escrito {curto(destino)}")
    else:
      print(f"inalterado {curto(destino)}")

  if args.check:
    for destino in stale:
      print(f"desatualizado: {curto(destino)}", file=sys.stderr)
    if stale:
      print("rode `task banner` e comite o resultado.", file=sys.stderr)
      return 1
    print("banner em dia com o baseline.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
