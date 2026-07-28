"""O banner do README é gerado, então precisa ser gerado do mesmo jeito sempre.

Um banner que muda de bytes sem o corpus mudar suja o `git diff` de todo commit
e tira o sentido do `--check`. O que estes testes protegem é essa promessa —
mesma entrada, mesmo SVG — mais o contrato de que cada questão aparece.
"""

import json
import re

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


def test_texto_do_svg_e_escapado():
  dados = json.loads(json.dumps(BASELINE_FAKE))
  dados["by_discipline"] = {"a & b <c>": 8, "fisica": 5}
  svg = banner.svg(dados, "dark")
  assert "a &amp; b &lt;c&gt;" in svg
  assert "<c>" not in svg
