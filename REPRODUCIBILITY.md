# Reprodutibilidade

Este documento é o procedimento para reconstruir o banco de questões do zero e
**verificar** que o resultado é o mesmo. Não basta seguir os passos: o último
deles compara o que você obteve com um baseline versionado e aponta exatamente
qual contagem divergiu, se divergir.

O pipeline tem cinco estágios. Os quatro primeiros são determinísticos dado o
mesmo ambiente. O quinto — a revisão humana — não é reproduzível por definição,
então é *registrado* em vez de recomputado: as decisões ficam em arquivos
versionados que o load reaplica.

```
extract ─→ transform ─→ [enrich] ─→ load ─→ verify
   │           │            │         ↑
   ↓           ↓            ↓         │
 PDFs      manifests     JSONs   decisions.json + audit.jsonl
                                  (revisão humana, versionada)
```

---

## Pré-requisitos, com versões

O que muda a saída, e portanto precisa bater:

| Componente | Versão de referência | Por que importa |
|---|---|---|
| Python | 3.10 | `Pipfile` fixa; `requires-python = ">=3.10"` |
| Dependências | `Pipfile.lock` | Instale com `pipenv install --dev`, **não** com `pipenv update` |
| PyMuPDF / MuPDF | 1.28.0 / 1.29.0 | Extração de texto e rasterização dos recortes |
| Tesseract | 5.5.3 | OCR de 11 documentos digitalizados |
| Idiomas do Tesseract | `por`, `eng` | `brew install tesseract-lang` |
| Google Chrome | qualquer recente | Só o extract usa; o chromedriver é resolvido pelo Selenium |

As versões efetivas de cada execução ficam gravadas em
`data/processed/provenance.json` e dentro do baseline. Se o `verify` acusar
divergência, ele compara os dois ambientes e mostra o que mudou.

```bash
brew install go-task tesseract tesseract-lang
export TESSDATA_PREFIX=$(brew --prefix)/share/tessdata   # coloque no seu shell
pipenv install --dev
cp .env.example .env
```

> **Sem Tesseract o pipeline roda**, mas 11 documentos ficam sem detecção e o
> resultado não bate com o baseline. O relatório de cobertura diz quais são e
> imprime o comando de instalação.

---

## Passo 1 — Extract

```bash
task migrate
task extract
```

Raspa `vestibular.ita.br` e `civil.ita.br/posgrad` e baixa os PDFs.

**Esperado:** 116 PDFs de vestibular, 42 de pós, 1 falha conhecida
(`prova_ita_pos_2018_2o_semestre_matematica.pdf` é um 404 no servidor do ITA —
o link existe na página, o arquivo não).

**Registro:** `data/{vestibular,pos}/provas/_index.json` guarda, por arquivo, de
qual das três tabelas históricas do site ele veio, mais a URL e o sha256. **Esse
sidecar é a única coisa que desambigua as eras** — a coluna `fisica` significa
prova de fase única em 2008–2018 e prova de 2ª fase de 2019 em diante.
`download_report.json` registra sucessos e falhas.

> **Se o site do ITA mudar**, o scraper quebra (depende de XPaths de uma página
> com `<frame>` de 2008). Os `_index.json` e os PDFs em disco bastam para
> reproduzir tudo a partir do passo 2 — vale arquivá-los.

## Passo 2 — Transform

```bash
task transform
```

Localiza as questões, monta os retângulos, exporta recortes e textos.

**Esperado:** `total de questoes: 4349`, `{'ok': 152, 'essay': 6}`, **zero
`empty`**. O total inclui as respostas dos gabaritos; as questões propriamente
ditas são 2730.

**Registro:** um `manifest.json` por documento com o sha256 do PDF de origem,
a versão do transform, os bboxes e o `source_sha256` de cada questão.
`coverage_report.json` consolida e lista os avisos.

Se algum documento aparecer como `empty`, confira primeiro o `TESSDATA_PREFIX`.

## Passo 3 — Enrich (opcional)

```bash
task enrich -- --dry-run          # estimativa de custo, não gasta nada
task enrich -- --limit 5          # amostra
task enrich                       # o resto
```

**Não é necessário para reproduzir o baseline** — ele foi gravado sem enrich.
Quem rodar o enrich obtém um fingerprint diferente, e deve gravar o próprio
baseline depois.

**Registro:** `data/cache/enrich/` guarda cada resposta indexada por
`sha256(conteúdo | versão do prompt | modelo)`. Reexecutar não gasta nada e
produz o mesmo resultado; mudar o prompt invalida o cache por construção.

## Passo 4 — Load

```bash
task load -- --rebuild
```

Apaga o `.db`, reaplica as migrations, carrega os manifests, projeta o gabarito
e **reaplica as decisões de revisão**.

**Esperado:** `158 documentos, 2730 questoes, 1619 respostas de gabarito,
1509 alternativas corretas marcadas`.

## Passo 5 — Verify

```bash
task verify
```

```
esperado : c23b7220c11d7dc7
obtido   : c23b7220c11d7dc7

OK — o estado bate com o baseline.
```

Se divergir, ele imprime a lista de contagens que mudaram e, se o ambiente
também mudou, o que mudou nele:

```
DIVERGENCIA:
  counts.questions: esperado 2730, obtido 2727 (-3)
  by_discipline.matematica: esperado 981, obtido 978 (-3)

O ambiente também mudou — provável causa:
  tesseract.version: esperado tesseract 5.5.3, obtido tesseract 5.4.1
```

---

## A revisão humana como passo do pipeline

As correções feitas na interface (`task review`) **fazem parte do
procedimento**, não são um ajuste avulso. Elas ficam em três arquivos
versionados, os únicos conteúdos de `data/` que o git rastreia:

| Arquivo | O que é |
|---|---|
| `data/review/decisions.json` | Estado final por questão. **É o que o load aplica.** |
| `data/review/audit.jsonl` | Trilha append-only: cada ação, com o antes e o depois. |
| `data/review/baseline.json` | As contagens esperadas e o ambiente que as produziu. |

Uma decisão é chaveada por `(caminho do PDF, número da questão)` — legível e
estável entre reconstruções, ao contrário do `questions.id`, que é
autoincremento e muda a cada `--rebuild`.

O `decisions.json` tem duas seções, porque são duas categorias com regras de
replay diferentes.

**`questions`** — decisões *sobre* uma questão que existe, mescladas campo a
campo:

| Campo | Efeito no reload |
|---|---|
| `status` | Define `review_status`. `human_verified` protege a linha do upsert e do LLM. |
| `needs_llm` | Override da triagem: força ou dispensa o enrich naquela questão. |
| `statement_markdown` / `alternatives` | Texto corrigido à mão, reaplicado como `human_verified`. |
| `bbox` | Recorte corrigido. O PNG é regerado no momento da correção; o reload realinha as linhas do banco. |
| `figures` | Figuras impressas longe do enunciado, associadas à mão. A lista é substituída, não mesclada, senão uma figura removida voltaria. Os PNGs são regerados dos retângulos no reload. |

**`operations`** — decisões que mudam *quais* questões existem. É uma lista
ordenada, não um mapa, porque o resultado depende da ordem: renumerar 5→6 e
depois 6→7 não é o mesmo que o contrário.

| Operação | Efeito no reload |
|---|---|
| `create` | Recria a questão a partir do retângulo. O PNG é regerado do bbox, nunca lido do disco, para que o artefato corresponda à decisão registrada. |
| `delete` | Remove a questão. O PNG fica em disco — apagar arquivo é irreversível e um recorte órfão não custa nada. |
| `renumber` | Move o número, trocando com a ocupante se houver. |

> **Renumerar move a própria chave da decisão.** A chave é `(caminho, número)`,
> então uma questão corrigida à mão como nº 5 que vira nº 6 perderia a correção
> se a chave ficasse para trás. O registro da operação migra as duas coisas
> juntas, e o replay roda **antes** das decisões por questão exatamente por
> isso.

Uma questão criada à mão nasce `human_verified`: veio de alguém apontando o
retângulo, então não faz sentido pedir que outra pessoa a aprove depois. Se o
transform passar a detectá-la sozinho, a detecção vence — ela é reproduzível e
o retângulo manual deixa de ser necessário.

**Por que isso importa para quem reproduz:** quem clonar o repositório e rodar
o pipeline obtém automaticamente as mesmas correções, sem repetir a auditoria.
O `audit.jsonl` permite conferir *o que* foi mudado à mão e questionar qualquer
decisão individual — a auditoria em si é auditável.

### Depois de auditar

```bash
task verify -- --snapshot     # grava o novo estado como baseline
git add data/review/ && git commit -m "auditoria: <o que foi revisado>"
```

O `--snapshot` deve ser um ato deliberado, num commit próprio: ele redefine o
que conta como "correto". Nunca o rode para silenciar uma divergência que você
não entendeu.

---

## O que é determinístico e o que não é

| | Determinístico? | Observação |
|---|---|---|
| Extract | Não | Depende do site do ITA. Os PDFs em disco congelam o resultado. |
| Transform | Sim | Dado os mesmos PDFs e as mesmas versões de PyMuPDF e Tesseract. |
| OCR | Sim, na prática | Mesma versão do Tesseract, mesmo DPI (300), mesmos idiomas. Versões diferentes mudam o texto reconhecido. |
| Enrich | Não | O LLM é amostral. O cache em disco congela o resultado de uma execução. |
| Load | Sim | Upsert por chave natural, sem dependência de ordem. |
| Revisão | Não aplicável | Registrada, não recomputada. |

Os `id` autoincremento **mudam** a cada `--rebuild` e por isso nenhum deles
entra no fingerprint. Compare por chave natural, nunca por id.

---

## Reproduzir sem baixar os PDFs de novo

O `data/` é gitignored (exceto `data/review/`), então um clone limpo não tem o
corpus. Para transferir sem depender do site:

```bash
tar czf ita-corpus.tar.gz data/vestibular data/pos     # ~108 MB
# na outra máquina, depois de extrair:
task transform && task load -- --rebuild && task verify
```

Os `_index.json` vão junto, então a desambiguação das eras é preservada.
