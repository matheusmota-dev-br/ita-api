"""O banner do README é gerado, então precisa ser gerado do mesmo jeito sempre.

Um banner que muda de bytes sem o corpus mudar suja o `git diff` de todo commit
e tira o sentido do `--check`. O que estes testes protegem é essa promessa —
mesma entrada, mesmo SVG — mais o contrato de que cada questão aparece.
"""

import json
import re
import struct
import zlib

import pytest

from tools import banner

BASELINE_FAKE = {
  "by_year": {"2024": 10, "2025": 3},
  "by_discipline": {"matematica": 8, "fisica": 5},
  "counts": {
    "documents": 2,
    "exams": 2,
    "questions": 13,
    "alternatives_with_text": 40,
    "questions_with_answer": 6,
    "questions_with_discipline": 13,
    "questions_with_image": 13,
  },
  "fingerprint": "abc123def456",
}


@pytest.fixture
def baseline(tmp_path):
  caminho = tmp_path / "baseline.json"
  caminho.write_text(json.dumps(BASELINE_FAKE), encoding="utf-8")
  return caminho


def quadrados(svg: str) -> int:
  """Conta os quadradinhos: um `M` por questão, somando todas as trilhas.

  Só dentro dos `d=`: o nome das fontes (`SFMono`, `Menlo`) também tem `M`.
  """
  return sum(d.count("M") for d in re.findall(r'\sd="([^"]*)"', svg))


@pytest.mark.parametrize("tema", sorted(banner.THEMES))
def test_svg_e_deterministico(tema):
  assert banner.svg(BASELINE_FAKE, tema) == banner.svg(BASELINE_FAKE, tema)


@pytest.mark.parametrize("tema", sorted(banner.THEMES))
def test_um_quadrado_por_questao(tema):
  svg = banner.svg(BASELINE_FAKE, tema)
  assert quadrados(svg) == BASELINE_FAKE["counts"]["questions"]


def test_temas_diferem_so_nas_cores():
  """Mesma geometria nos dois temas — o claro não é o escuro com outro layout."""
  escuro = banner.svg(BASELINE_FAKE, "dark")
  claro = banner.svg(BASELINE_FAKE, "light")
  assert escuro != claro
  assert quadrados(escuro) == quadrados(claro)
  assert escuro.count("<path") == claro.count("<path")


def test_cabecalho_traz_os_numeros_do_baseline():
  svg = banner.svg(BASELINE_FAKE, "dark")
  assert "13 questões" in svg
  assert "2024–2025" in svg
  assert "46% com gabarito" in svg  # 6/13 arredondado pra baixo
  assert "100% com recorte" in svg
  assert "baseline abc123de" in svg


def test_materias_saem_da_maior_pra_menor():
  svg = banner.svg(BASELINE_FAKE, "dark")
  assert svg.index("matemática 8") < svg.index("física 5")


def test_pct_nao_arredonda_pra_cima():
  assert banner.pct(129, 130) == "99%"
  assert banner.pct(130, 130) == "100%"
  assert banner.pct(1, 0) == "—"


def test_escreve_os_dois_temas_e_check_aprova(baseline, tmp_path, capsys):
  saida = tmp_path / "docs"
  argv = ["--baseline", str(baseline), "--out-dir", str(saida)]
  assert banner.main(argv) == 0
  gerados = sorted(p.name for p in saida.glob("*.svg"))
  assert gerados == ["banner-dark.svg", "banner-light.svg"]
  assert banner.main([*argv, "--check"]) == 0

  # Rodar de novo não reescreve o que já está igual.
  capsys.readouterr()
  assert banner.main(argv) == 0
  assert "inalterado" in capsys.readouterr().out


def test_check_reprova_svg_desatualizado(baseline, tmp_path):
  saida = tmp_path / "docs"
  argv = ["--baseline", str(baseline), "--out-dir", str(saida)]
  assert banner.main(argv) == 0
  (saida / "banner-dark.svg").write_text("<svg/>", encoding="utf-8")
  assert banner.main([*argv, "--check"]) == 1


def test_check_reprova_svg_ausente(baseline, tmp_path):
  argv = ["--baseline", str(baseline), "--out-dir", str(tmp_path / "vazio"), "--check"]
  assert banner.main(argv) == 1


def test_baseline_ausente_ou_vazio_nao_desenha_nada(tmp_path):
  faltando = tmp_path / "nao-existe.json"
  assert banner.main(["--baseline", str(faltando)]) == 2

  vazio = tmp_path / "vazio.json"
  vazio.write_text(json.dumps({"counts": {}, "by_year": {}}), encoding="utf-8")
  assert banner.main(["--baseline", str(vazio)]) == 2


def test_rotulo_de_materia_estreita_e_omitido():
  """Rótulo que não cabe no segmento sai de cena, em vez de invadir o vizinho."""
  dados = json.loads(json.dumps(BASELINE_FAKE))
  dados["by_discipline"] = {"matematica": 2000, "portugues": 1, "ingles": 1}
  svg = banner.svg(dados, "dark")
  assert "matemática 2.000" in svg
  assert "português" not in svg


def _paeth(a, b, c):
  pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
  return a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)


def escreve_png(caminho, larg, alt, pixels, canais=4, filtro=0, bits=8, cor=None):
  """Codifica um PNG aplicando o filtro pedido — o inverso do que o banner faz.

  Existe para exercitar os cinco filtros do formato de verdade: um decodificador
  que só foi testado com filtro 0 passa sem nunca ter rodado o Paeth, que é
  justamente o caminho onde um `& 0xFF` esquecido só aparece em imagem real.
  """
  cor = (6 if canais == 4 else 2) if cor is None else cor
  passo = larg * canais
  cru = bytes(pixels)
  linhas = bytearray()
  for y in range(alt):
    linha = cru[y * passo : (y + 1) * passo]
    ant = cru[(y - 1) * passo : y * passo] if y else bytes(passo)
    saida = bytearray()
    for i, valor in enumerate(linha):
      esq = linha[i - canais] if i >= canais else 0
      cima = ant[i]
      diag = ant[i - canais] if i >= canais else 0
      pred = {
        0: 0,
        1: esq,
        2: cima,
        3: (esq + cima) >> 1,
        4: _paeth(esq, cima, diag),
      }[filtro]
      saida.append((valor - pred) & 0xFF)
    linhas.append(filtro)
    linhas += saida

  def chunk(tipo, dados):
    return (
      struct.pack(">I", len(dados))
      + tipo
      + dados
      + struct.pack(">I", zlib.crc32(tipo + dados) & 0xFFFFFFFF)
    )

  ihdr = struct.pack(">IIBBBBB", larg, alt, bits, cor, 0, 0, 0)
  caminho.write_bytes(
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", ihdr)
    + chunk(b"IDAT", zlib.compress(bytes(linhas)))
    + chunk(b"IEND", b"")
  )
  return caminho


@pytest.mark.parametrize("filtro", [0, 1, 2, 3, 4])
def test_decodifica_os_cinco_filtros_do_png(tmp_path, filtro):
  larg, alt = 5, 4
  # Gradiente com virada de 255 -> 0, que é onde o módulo 256 do desfiltro pesa.
  pixels = bytes(
    (x * 61 + y * 97 + ch * 29) % 256
    for y in range(alt)
    for x in range(larg)
    for ch in range(4)
  )
  png = escreve_png(tmp_path / f"f{filtro}.png", larg, alt, pixels, filtro=filtro)
  assert banner._le_png(png) == (larg, alt, pixels, 4)


def test_decodifica_png_sem_canal_alfa(tmp_path):
  pixels = bytes(range(3 * 2 * 3))
  png = escreve_png(tmp_path / "rgb.png", 3, 2, pixels, canais=3, filtro=1)
  assert banner._le_png(png) == (3, 2, pixels, 3)


def test_png_recusa_o_que_nao_sabe_ler(tmp_path):
  naopng = tmp_path / "x.png"
  naopng.write_bytes(b"GIF89a" + b"\x00" * 40)
  with pytest.raises(ValueError, match="não é PNG"):
    banner._le_png(naopng)

  # Paleta (tipo 3) não é suportada, e desenhar como se fosse RGB daria lixo.
  paleta = escreve_png(tmp_path / "p.png", 2, 2, bytes(12), canais=3, cor=3)
  with pytest.raises(ValueError, match="8 bits RGB/RGBA"):
    banner._le_png(paleta)


def test_png_com_filtro_invalido_reclama(tmp_path):
  png = escreve_png(tmp_path / "f9.png", 2, 2, bytes(16), filtro=0)
  bruto = bytearray(png.read_bytes())
  # Recomprime o IDAT com um byte de filtro inexistente na primeira scanline.
  ini = bruto.index(b"IDAT") + 4
  (tam,) = struct.unpack(">I", bruto[ini - 8 : ini - 4])
  dados = bytearray(zlib.decompress(bytes(bruto[ini : ini + tam])))
  dados[0] = 9
  novo = zlib.compress(bytes(dados))
  png.write_bytes(
    bytes(bruto[: ini - 8])
    + struct.pack(">I", len(novo))
    + b"IDAT"
    + novo
    + struct.pack(">I", zlib.crc32(b"IDAT" + novo) & 0xFFFFFFFF)
    + bytes(bruto[ini + tam + 4 :])
  )
  with pytest.raises(ValueError, match="filtro 9"):
    banner._le_png(png)


def test_emblema_recorta_na_caixa_de_tinta(tmp_path):
  """Margem transparente não vira célula: a grade sai colada na tinta."""
  larg, alt = 12, 12
  px = bytearray(larg * alt * 4)
  for y in range(4, 8):
    for x in range(2, 10):  # bloco 8x4 de preto opaco, deslocado do canto
      o = (y * larg + x) * 4
      px[o : o + 4] = bytes((0, 0, 0, 255))
  png = escreve_png(tmp_path / "bloco.png", larg, alt, bytes(px), filtro=0)
  grade = banner.emblema_grade(png, cols=8, limpar=False)
  assert len(grade[0]) == 8
  assert len(grade) == 4  # 8 colunas * (4/8) de razão
  assert all(all(linha) for linha in grade)  # tudo aceso: o recorte é só a tinta


def test_emblema_ignora_o_branco(tmp_path):
  """Branco opaco é fundo, e não tinta — é o que preserva os vãos das penas."""
  larg = alt = 4
  px = bytearray()
  for y in range(alt):
    for x in range(larg):
      px += bytes((255, 255, 255, 255) if x >= 2 else (0, 0, 0, 255))
  png = escreve_png(tmp_path / "meio.png", larg, alt, bytes(px), filtro=0)
  grade = banner.emblema_grade(png, cols=2, limpar=False)
  # A prova está na razão: a caixa de tinta é a metade escura (2x4), então com 2
  # colunas saem 4 linhas. Se o branco contasse, a caixa seria 4x4 e daria 2.
  assert (len(grade[0]), len(grade)) == (2, 4)
  assert all(all(linha) for linha in grade)


def test_emblema_sem_tinta_reclama(tmp_path):
  vazio = escreve_png(tmp_path / "vazio.png", 3, 3, bytes(36), filtro=0)
  with pytest.raises(ValueError, match="nenhum pixel de tinta"):
    banner.emblema_grade(vazio)


@pytest.mark.parametrize(
  "rgb,esperado",
  [
    ((255, 203, 5), banner.OURO),  # asa
    ((0, 102, 179), banner.AZUL),  # escudo e letras
    ((236, 28, 36), banner.VERMELHO),  # tocha
    ((0, 0, 0), banner.CONTORNO),  # traço
    ((255, 255, 255), 0),  # branco é fundo, não tinta
    ((240, 240, 240), 0),  # quase-branco também
  ],
)
def test_classifica_as_cores_do_emblema(rgb, esperado):
  assert banner._classe_de_cor(*rgb) == esperado


def test_vermelho_vence_o_alaranjado_da_borda_da_chama():
  """A ordem dos testes importa: laranja tem R alto e passaria por ouro."""
  assert banner._classe_de_cor(230, 90, 40) == banner.VERMELHO


def test_celula_recebe_a_cor_que_domina(tmp_path):
  """Maioria por célula, e o contorno só ganha quando não há cor nenhuma."""
  larg = alt = 4
  px = bytearray()
  for y in range(alt):
    for x in range(larg):
      # 3/4 azul, 1/4 vermelho: a célula toda deve sair azul.
      px += bytes((236, 28, 36, 255) if (x, y) == (0, 0) else (0, 102, 179, 255))
  png = escreve_png(tmp_path / "mistura.png", larg, alt, bytes(px), filtro=0)
  assert banner.emblema_grade(png, cols=1, limpar=False) == [[banner.AZUL]]

  # Só preto: aí sim o contorno.
  preto = escreve_png(tmp_path / "preto.png", 2, 2, bytes((0, 0, 0, 255) * 4), filtro=0)
  assert banner.emblema_grade(preto, cols=1, limpar=False) == [[banner.CONTORNO]]


# G=ouro, B=azul, R=vermelho, K=contorno. Uma letra cada, para as grades
# dos testes ficarem legíveis — e nenhuma delas é `O`, que se confunde com 0.
G, B, R, K = banner.OURO, banner.AZUL, banner.VERMELHO, banner.CONTORNO


def test_contorno_assume_a_cor_da_vizinhanca():
  grade = [
    [G, G, G],
    [G, K, G],
    [G, G, G],
  ]
  assert banner._sem_contorno(grade)[1][1] == G

  # Desenho que é só traço continua traço: não há cor para herdar.
  assert banner._sem_contorno([[K, K], [K, K]]) == [[K, K], [K, K]]


def test_suaviza_cor_nao_acende_nem_apaga():
  grade = [
    [B, B, B],
    [B, R, B],  # vermelho perdido no meio do escudo
    [B, B, B],
  ]
  limpo = banner._suaviza_cor(grade)
  assert limpo[1][1] == B
  assert [[bool(v) for v in linha] for linha in limpo] == [
    [bool(v) for v in linha] for linha in grade
  ]


def test_tira_orfas_come_a_farpa_e_poupa_o_bloco():
  grade = [
    [G, G, 0, 0],
    [G, G, 0, G],  # o G solto à direita tem 0 vizinhos acesos
    [0, 0, 0, 0],
  ]
  limpo = banner._tira_orfas(grade, 3)
  assert limpo[1][3] == 0
  assert limpo[0][0] == G and limpo[1][1] == G


def test_fecha_furo_de_alfinete_mas_nao_o_vao_entre_letras():
  furo = [
    [B, B, B],
    [B, 0, B],  # 8 vizinhos acesos: furo
    [B, B, B],
  ]
  assert banner._fecha_furos(furo)[1][1] == B

  # Vão de duas células: 7 vizinhas acesas cada, uma a menos que o cerco
  # completo. É o que mantém as letras separadas em vez de virar bloco azul —
  # e é exatamente por isso que o limiar é 8, e não 7.
  vao = [
    [B, B, B, B],
    [B, 0, 0, B],
    [B, B, B, B],
  ]
  assert banner._fecha_furos(vao) == vao
  assert banner._fecha_furos(vao, 7) != vao


def test_achata_o_topo_da_asa_e_poupa_a_tocha():
  """Nada de ouro acima da primeira linha longa; o fogo continua subindo."""
  grade = [
    [0, 0, R, 0, 0, 0],  # tocha
    [0, G, R, 0, 0, G],  # voltinhas de ouro que sobem
    [G, G, G, G, G, G],  # a linha longa: o topo da asa
    [G, G, G, G, G, G],
  ]
  limpo = banner._achata_topo_da_asa(grade, fracao=0.5)
  assert limpo[1] == [0, 0, R, 0, 0, 0]
  assert limpo[0] == [0, 0, R, 0, 0, 0]
  assert limpo[2] == [G] * 6

  # Sem linha longa de ouro não há o que achatar, e nada é apagado.
  so_tocha = [[0, R, 0], [0, R, 0]]
  assert banner._achata_topo_da_asa(so_tocha, fracao=0.5) == so_tocha


def test_recorta_borda_vazia():
  grade = [
    [0, 0, 0, 0],
    [0, G, G, 0],
    [0, 0, 0, 0],
  ]
  assert banner._recorta(grade) == [[G, G]]
  # Grade vazia volta como está, em vez de estourar.
  assert banner._recorta([[0, 0]]) == [[0, 0]]


def test_emblema_real_e_deterministico():
  """O PNG está versionado, então a grade dele é fixa — é o que prende o SVG."""
  a = banner.emblema_grade()
  b = banner.emblema_grade()
  assert a == b
  assert (len(a[0]), len(a)) == (62, 22)  # 64x24 cru, menos a borda que a limpeza abre
  assert sum(1 for linha in a for v in linha if v) == 623
  # As três cores do emblema aparecem: se a classificação regredir para
  # luminância, escudo e tocha voltam a virar a mesma mancha.
  presentes = {v for linha in a for v in linha if v}
  assert presentes == {banner.OURO, banner.AZUL, banner.VERMELHO}


def test_limpeza_muda_o_emblema_real_no_sentido_certo():
  cru = banner.emblema_grade(limpar=False)
  limpo = banner.emblema_grade()
  assert (len(cru[0]), len(cru)) == (64, 24)

  # G traço cinza existe no cru e não sobra nenhum no limpo.
  assert sum(1 for linha in cru for v in linha if v == K) == 11
  assert not any(v == K for linha in limpo for v in linha)

  # Nada de ouro acima do topo da asa: só a tocha sobe.
  topo = next(
    r for r, linha in enumerate(limpo) if linha.count(G) >= len(limpo[0]) * 0.5
  )
  acima = [v for linha in limpo[:topo] for v in linha if v]
  assert acima and set(acima) == {R}

  # E sobrou menos farpa. Não zero: `_tira_orfas` é uma passada só, de propósito
  # — se cascateasse, apagar uma ponta afinaria a vizinha e a asa iria embora
  # inteira. G que se afirma é a direção.
  def soltas(g):
    return sum(
      1
      for r, linha in enumerate(g)
      for c, v in enumerate(linha)
      if v and sum(1 for n in banner._vizinhos(g, r, c) if n) < 4
    )

  assert soltas(limpo) < soltas(cru)
  assert soltas(limpo) <= 4


@pytest.mark.parametrize("tema", sorted(banner.THEMES))
def test_todo_tema_tem_cor_para_toda_classe(tema):
  """Classe sem cor no tema viraria KeyError na hora de desenhar."""
  paleta = banner.THEMES[tema]["emblema"]
  assert set(paleta) == {banner.OURO, banner.AZUL, banner.VERMELHO, banner.CONTORNO}
  assert all(re.fullmatch(r"#[0-9a-f]{6}", v) for v in paleta.values())


def test_svg_desenha_o_emblema_so_quando_recebe_a_grade():
  grade = banner.emblema_grade()
  cores = len({v for linha in grade for v in linha if v})
  sem = banner.svg(BASELINE_FAKE, "dark")
  com = banner.svg(BASELINE_FAKE, "dark", grade)
  assert com.count("<path") == sem.count("<path") + cores
  assert quadrados(com) == quadrados(sem) + 623


def test_emblema_ausente_nao_impede_o_banner(baseline, tmp_path, capsys):
  argv = [
    "--baseline",
    str(baseline),
    "--out-dir",
    str(tmp_path / "docs"),
    "--logo",
    str(tmp_path / "nao-existe.png"),
  ]
  assert banner.main(argv) == 0
  assert "emblema ausente" in capsys.readouterr().out
  assert (tmp_path / "docs" / "banner-dark.svg").exists()


def test_emblema_ilegivel_falha_em_vez_de_desenhar_errado(baseline, tmp_path, capsys):
  ruim = tmp_path / "ruim.png"
  ruim.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
  argv = [
    "--baseline",
    str(baseline),
    "--out-dir",
    str(tmp_path / "d"),
    "--logo",
    str(ruim),
  ]
  assert banner.main(argv) == 2
  assert "ilegível" in capsys.readouterr().err


def test_preview_do_emblema_nao_escreve_svg(tmp_path, capsys):
  saida = tmp_path / "docs"
  assert banner.main(["--preview-emblema", "--out-dir", str(saida)]) == 0
  out = capsys.readouterr().out
  assert "62x22 células" in out
  assert "█" in out
  assert not saida.exists()


def test_texto_do_svg_e_escapado():
  dados = json.loads(json.dumps(BASELINE_FAKE))
  dados["by_discipline"] = {"a & b <c>": 8, "fisica": 5}
  svg = banner.svg(dados, "dark")
  assert "a &amp; b &lt;c&gt;" in svg
  assert "<c>" not in svg
