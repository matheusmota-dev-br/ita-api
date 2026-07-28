"""Gera o banner do README a partir do baseline versionado.

Deterministico por construcao: as entradas sao `data/review/baseline.json` — o
mesmo arquivo que o `task verify` compara — e `tools/assets/ita-logo.png`, as
duas versionadas. Nada de random, nada de data/hora, nada de ler o `.db` (que e
gitignored) e nada de rede: dois clones do repo produzem SVGs byte a byte
identicos, e o `git diff` do banner so muda quando o corpus muda.

  PYTHONPATH=. pipenv run python -m tools.banner            # regrava os temas
  PYTHONPATH=. pipenv run python -m tools.banner --check     # falha se stale
  PYTHONPATH=. pipenv run python -m tools.banner --preview-emblema

Cada questao do corpus e um quadradinho. As torres sao os anos; a barra de
baixo e a divisao por materia; e o emblema a esquerda e um mosaico da mesma
grade, amostrado do PNG com stdlib e pintado por matiz — nao a imagem embutida.

O emblema e marca do ITA, aqui como referencia a origem das provas. Projeto
independente, sem vinculo nem endosso do Instituto Tecnologico de Aeronautica.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import unicodedata
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "data" / "review" / "baseline.json"
LOGO = ROOT / "tools" / "assets" / "ita-logo.png"
OUT_DIR = ROOT / "docs"

# Geometria, em unidades do viewBox.
W, H = 1200, 420
PAD = 56
TOWER_COLS = 8  # quadradinhos por linha de uma torre
CELL = 6.0  # passo da grade
DOT = 4.4  # lado do quadradinho (CELL - DOT = respiro de 1.6)
BASELINE_Y = 306.0  # onde as torres se apoiam
BAR_Y, BAR_H = 350.0, 14.0

# O emblema, em mosaico da mesma grade das questoes, num passo mais fino.
# 64 celulas e onde a tocha se separa do escudo e os vaos entre as letras
# aparecem; abaixo disso o miolo vira um bloco azul so.
EMBLEMA_COLS = 64  # largura em celulas; a altura sai da razao do PNG
EMBLEMA_CELL = 3.0
EMBLEMA_DOT = 2.2
EMBLEMA_TOPO = 50.0
# Ele fica a esquerda, e o bloco de texto comeca depois dele.
TEXTO_X = PAD + EMBLEMA_COLS * EMBLEMA_CELL + 28
# Opaco e nao-branco conta como tinta. O limiar deixa o branco de fora, o que
# preserva os vaos entre as penas; 0.45 de cobertura da celula acende o quadrado.
EMBLEMA_LUM_MAX = 228
EMBLEMA_COBERTURA = 0.45

# As classes de cor do emblema. A primeira versao classificava por luminancia e
# perdia o desenho: azul (~99) e vermelho (~72) caem os dois no "escuro", entao
# escudo e tocha viravam a mesma mancha. Matiz separa.
OURO, AZUL, VERMELHO, CONTORNO = 1, 2, 3, 4

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
    # Cores do proprio emblema, amostradas do PNG. O azul sobe um passo em dark:
    # o #0066b3 original fica em 2,98:1 contra este fundo e o escudo — a parte
    # que carrega as letras — apagava.
    "emblema": {
      OURO: "#ffcb05",
      AZUL: "#1477c9",
      VERMELHO: "#ec1c24",
      CONTORNO: "#7d7c73",
    },
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
    # Em light o ouro do emblema escurece: #ffcb05 sobre #fcfcfb da 1,47:1 e as
    # asas somem. O logo oficial resolve isso com contorno preto, que nesta
    # resolucao nao existe, entao o pigmento e que tem de descer.
    "emblema": {
      OURO: "#c08a00",
      AZUL: "#0066b3",
      VERMELHO: "#d0121a",
      CONTORNO: "#3a3a36",
    },
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


def _le_png(caminho: Path) -> tuple[int, int, bytes, int]:
  """Decodifica um PNG RGB/RGBA de 8 bits, nao-entrelaçado, com stdlib.

  O gerador precisa continuar sem dependencia: o passo do CI que confere o
  banner roda num job que nao instala PyMuPDF nem Pillow, e depender de um
  decodificador externo colocaria a saida a merce da versao dele. `zlib` mais o
  desfiltro do proprio PNG dao o mesmo pixel em qualquer maquina.

  Levanta `ValueError` para o que nao sabe ler, em vez de desenhar errado.
  """
  b = caminho.read_bytes()
  if b[:8] != b"\x89PNG\r\n\x1a\n":
    raise ValueError(f"{caminho.name}: não é PNG")
  pos, idat, cabecalho = 8, bytearray(), None
  while pos + 8 <= len(b):
    (tam,) = struct.unpack(">I", b[pos : pos + 4])
    tipo = b[pos + 4 : pos + 8]
    dados = b[pos + 8 : pos + 8 + tam]
    if tipo == b"IHDR":
      larg, alt, bits, cor, _, _, entrelace = struct.unpack(">IIBBBBB", dados)
      if bits != 8 or cor not in (2, 6) or entrelace:
        raise ValueError(
          f"{caminho.name}: só PNG de 8 bits RGB/RGBA não-entrelaçado "
          f"(bits={bits}, tipo={cor}, entrelace={entrelace})"
        )
      cabecalho = (larg, alt, 4 if cor == 6 else 3)
    elif tipo == b"IDAT":
      idat += dados
    elif tipo == b"IEND":
      break
    pos += 12 + tam
  if cabecalho is None:
    raise ValueError(f"{caminho.name}: sem IHDR")

  larg, alt, canais = cabecalho
  cru = zlib.decompress(bytes(idat))
  passo = larg * canais
  if len(cru) < alt * (passo + 1):
    raise ValueError(f"{caminho.name}: IDAT truncado")

  # Desfiltro: cada scanline vem com um dos cinco filtros do PNG no primeiro
  # byte, e cada um se resolve contra o pixel da esquerda e/ou a linha de cima.
  saida = bytearray(alt * passo)
  anterior = bytearray(passo)
  p = 0
  for y in range(alt):
    filtro = cru[p]
    p += 1
    linha = bytearray(cru[p : p + passo])
    p += passo
    if filtro == 1:  # Sub
      for i in range(canais, passo):
        linha[i] = (linha[i] + linha[i - canais]) & 0xFF
    elif filtro == 2:  # Up
      for i in range(passo):
        linha[i] = (linha[i] + anterior[i]) & 0xFF
    elif filtro == 3:  # Average
      for i in range(passo):
        esq = linha[i - canais] if i >= canais else 0
        linha[i] = (linha[i] + ((esq + anterior[i]) >> 1)) & 0xFF
    elif filtro == 4:  # Paeth
      for i in range(passo):
        a = linha[i - canais] if i >= canais else 0
        cima = anterior[i]
        diag = anterior[i - canais] if i >= canais else 0
        pa, pb, pc = abs(cima - diag), abs(a - diag), abs(a + cima - 2 * diag)
        pr = a if (pa <= pb and pa <= pc) else (cima if pb <= pc else diag)
        linha[i] = (linha[i] + pr) & 0xFF
    elif filtro != 0:
      raise ValueError(f"{caminho.name}: filtro {filtro} desconhecido na linha {y}")
    saida[y * passo : (y + 1) * passo] = linha
    anterior = linha
  return larg, alt, bytes(saida), canais


def _classe_de_cor(r: int, g: int, b: int) -> int:
  """A qual parte do emblema este pixel pertence, por matiz.

  A ordem dos testes importa: o vermelho da tocha e o azul do escudo tem
  luminancia parecida (~72 e ~99), entao quem separa e a dominancia de canal, e
  o vermelho vem antes porque o alaranjado da borda da chama tambem tem R alto.
  """
  if 0.2126 * r + 0.7152 * g + 0.0722 * b >= EMBLEMA_LUM_MAX:
    return 0  # branco: fundo, e o que preserva os vaos entre as penas
  if r > 110 and r > g * 1.7 and r > b * 1.7:
    return VERMELHO
  if b > r + 40 and b > 90:
    return AZUL
  if r > 140 and g > 90 and b < 130:
    return OURO
  return CONTORNO  # preto do traco e o antialias entre as areas


def emblema_grade(caminho: Path = LOGO, cols: int = EMBLEMA_COLS) -> list[list[int]]:
  """Reduz o emblema a uma grade de classes de cor, recortada na caixa de tinta.

  Cada celula recebe a cor que domina os pixels dela — asas em ouro, escudo e
  letras em azul, tocha em vermelho. O contorno preto so vence quando a celula
  nao tem nenhuma das tres cores, senao o traco escuro do desenho comeria as
  areas de cor inteiras nessa resolucao.
  """
  larg, alt, px, canais = _le_png(caminho)
  classes = bytearray(larg * alt)
  for i in range(larg * alt):
    o = i * canais
    if canais == 4 and px[o + 3] < 128:
      continue
    classes[i] = _classe_de_cor(px[o], px[o + 1], px[o + 2])

  colunas = [x for x in range(larg) if any(classes[y * larg + x] for y in range(alt))]
  linhas = [y for y in range(alt) if any(classes[y * larg + x] for x in range(larg))]
  if not colunas or not linhas:
    raise ValueError(f"{caminho.name}: nenhum pixel de tinta")
  x0, x1 = colunas[0], colunas[-1] + 1
  y0, y1 = linhas[0], linhas[-1] + 1
  cx, cy = x1 - x0, y1 - y0
  rows = max(1, round(cols * cy / cx))

  # Voto de caixa por celula, e nao amostra pontual: com ~8px de fonte por
  # celula, um ponto no lugar errado apaga uma pena inteira.
  grade = []
  for r in range(rows):
    linha = []
    for c in range(cols):
      sx0 = x0 + cx * c // cols
      sx1 = max(sx0 + 1, x0 + cx * (c + 1) // cols)
      sy0 = y0 + cy * r // rows
      sy1 = max(sy0 + 1, y0 + cy * (r + 1) // rows)
      votos = [classes[y * larg + x] for y in range(sy0, sy1) for x in range(sx0, sx1)]
      tinta = len(votos) - votos.count(0)
      if tinta / len(votos) < EMBLEMA_COBERTURA:
        linha.append(0)
        continue
      coloridos = [(votos.count(k), k) for k in (OURO, AZUL, VERMELHO)]
      quanto, cor = max(coloridos)
      linha.append(cor if quanto else CONTORNO)
    grade.append(linha)
  return grade


def emblema(t: dict, grade: list[list[int]]) -> list[str]:
  """Desenha o mosaico à esquerda, em lockup com o wordmark: um path por cor."""
  trilhas: dict[int, list[str]] = {}
  for r, linha in enumerate(grade):
    for c, cor in enumerate(linha):
      if not cor:
        continue
      trilhas.setdefault(cor, []).append(
        f"M{PAD + c * EMBLEMA_CELL:.1f} {EMBLEMA_TOPO + r * EMBLEMA_CELL:.1f}"
        f"h{EMBLEMA_DOT:g}v{EMBLEMA_DOT:g}h-{EMBLEMA_DOT:g}z"
      )
  return [
    f'<path fill="{t["emblema"][cor]}" d="{"".join(trilhas[cor])}"/>'
    for cor in sorted(trilhas)
  ]


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


def svg(dados: dict, tema: str, grade_emblema: list[list[int]] | None = None) -> str:
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
    texto(TEXTO_X, 82, "ita-api", size=46, fill=t["ink"], weight="bold", spacing=-1.2),
    texto(
      TEXTO_X,
      108,
      "Banco de questões dos vestibulares e da pós do ITA, "
      "extraídas dos PDFs oficiais e servidas por uma API.",
      size=14,
      fill=t["ink_soft"],
    ),
    texto(
      TEXTO_X,
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
  if grade_emblema:
    partes += emblema(t, grade_emblema)
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
  ap.add_argument(
    "--logo",
    type=Path,
    default=LOGO,
    help="PNG do emblema; `--logo ''` desenha o banner sem ele",
  )
  ap.add_argument(
    "--preview-emblema",
    action="store_true",
    help="imprime o mosaico do emblema em texto e sai, sem escrever SVG",
  )
  args = ap.parse_args(argv)

  # O emblema é ornamento: se o PNG faltar, o banner sai sem ele em vez de o
  # gerador morrer. Um PNG ilegível, porém, é erro — significa que alguém trocou
  # o arquivo por algo que este decodificador não lê, e desenhar a silhueta
  # errada calado seria pior.
  grade = None
  if args.logo and Path(args.logo).exists():
    try:
      grade = emblema_grade(Path(args.logo))
    except ValueError as erro:
      print(f"emblema ilegível: {erro}", file=sys.stderr)
      return 2
  elif args.logo:
    print(f"emblema ausente ({curto(Path(args.logo))}), seguindo sem ele.")

  if args.preview_emblema:
    if not grade:
      print("sem emblema para pré-visualizar.", file=sys.stderr)
      return 2
    simbolos = {0: "  ", OURO: "░░", AZUL: "██", VERMELHO: "▓▓", CONTORNO: "::"}
    for linha in grade:
      print("".join(simbolos[v] for v in linha))
    acesos = sum(1 for linha in grade for v in linha if v)
    print(f"{EMBLEMA_COLS}x{len(grade)} células, {acesos} quadrados")
    return 0

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
    conteudo = svg(dados, tema, grade)
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
