<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/banner-dark.svg">
    <img src="docs/banner-light.svg" width="100%"
         alt="ita-api — 2.730 questões do ITA entre 2008 e 2026, extraídas de 158 PDFs oficiais. Cada quadrado é uma questão, as torres são os anos e a barra de baixo é a divisão por matéria.">
  </picture>
</p>

Banco de questões dos vestibulares e do processo seletivo de pós-graduação do
ITA, extraídas dos PDFs oficiais e servidas por uma API FastAPI.

O pipeline baixa as provas do site do ITA, recorta cada questão em imagem,
extrai o texto, parseia os gabaritos, opcionalmente normaliza tudo com um LLM e
carrega o resultado num arquivo SQLite. A API consome esse arquivo.

## Estado atual

| | |
|---|---|
| PDFs baixados | 158 (vestibular 2008–2026, pós 2011–2026) |
| Provas (ano + fase) | 57 |
| Questões extraídas | 2730, em 152 documentos |
| Documentos sem detecção | 0 |
| Questões com recorte e texto | 2730 (100%) |
| Questões com matéria identificada | 2730 (100%) |
| Alternativas com texto | 9252 |
| Respostas de gabarito | 1619, 99% com matéria identificada |
| Questões com resposta conhecida | 1479 (54%) |
| Documentos recuperados por OCR | 11 |

Esses números não são digitados à mão: saem de `data/review/baseline.json`, que
é o que o `task verify` compara e o que o banner acima desenha.

## Pré-requisitos

- Python 3.10, Pipenv, [Task](https://taskfile.dev) (`brew install go-task`)
- Google Chrome (o scraper usa Selenium; o chromedriver é resolvido sozinho)
- Tesseract, para os PDFs digitalizados: `brew install tesseract tesseract-lang`
  (Linux: `apt-get install tesseract-ocr tesseract-ocr-por`). Sem ele o
  pipeline roda, mas 11 documentos ficam sem detecção e dizem isso no relatório
- `ANTHROPIC_API_KEY` apenas para o estágio de enriquecimento

```bash
pipenv install --dev
cp .env.example .env
```

## Pipeline

```bash
task migrate                 # cria data/ita.db com o schema
task transform               # recorta as questões e parseia os gabaritos
task enrich -- --dry-run     # estima o custo do LLM antes de gastar
task load                    # carrega tudo no SQLite
task api                     # sobe a API em :8000
```

**Não há `task extract` aí de propósito: os 158 PDFs vêm no clone.** Eles estão
versionados em `data/{vestibular,pos}/` — 108 MB — junto dos `_index.json` que
os desambiguam. `task extract` continua existindo para buscar as provas de um ano
novo, e é idempotente: rodá-lo sobre o corpus versionado não muda um byte.

Cada estágio é idempotente: rodar de novo não refaz o que já está pronto.
`task etl` encadeia extract → transform → load.

### 1. Extract — `src/etl/extract/`

Raspa as três tabelas históricas de `vestibular.ita.br` e a lista de
`civil.ita.br/posgrad` com Selenium, e baixa cada PDF.

Grava um sidecar `_index.json` junto dos PDFs mapeando arquivo → formato, ano,
coluna, URL e sha256. **Isso é essencial**: a coluna `fisica` significa prova de
fase única em 2008–2018 e prova de 2ª fase de 2019 em diante, e o nome do
arquivo sozinho não distingue os dois casos.

Falhas de download entram em `download_report.json` e no exit code — um corpus
parcial não pode passar por completo.

O sha256 de cada arquivo fica no sidecar, então dá para conferir o corpus
versionado contra o que o ITA publicou sem baixar nada de novo — o script está em
[REPRODUCIBILITY.md](REPRODUCIBILITY.md). Hoje: 158 de 158 íntegros.

### 2. Transform — `src/etl/transform/`

Para cada PDF, localiza o início de cada questão, monta o retângulo que vai dali
até a questão seguinte, e exporta:

- `questoes/questao_NN.png` — o recorte, a 150 DPI
- `textos/questao_NN_dentro.txt` — o texto dentro do recorte
- `textos/questao_NN_fora.txt` — o contexto da página que está fora dele
  (o bloco "Convenções" de cada seção, textos base)
- `debug/questoes_regioes.pdf` — o PDF original com as caixas em vermelho
- `manifest.json` — o contrato com os estágios seguintes

O corpus tem dois estilos de rótulo: `Questão N.` (95 provas) e `N.` sozinho no
início da linha (19 provas, quase todas de pós). O segundo é ambíguo — a lista
de "Observações" da capa também começa com `1.` — então os candidatos numéricos
são encadeados em sequências e vence a que menos deixa candidatos para trás.

Três obstáculos do corpus são tratados aqui, em ordem de custo:

1. **Rótulos variados** — `Questão N`, `N.` sozinho, e `Question N` em inglês
   nas provas de pós.
2. **Fonte sem tabela ToUnicode** — a prova de química de 2018 sai como
   `&RQVWDQWH GH $YRJDGUR`. `text_repair.py` detecta que é um deslocamento
   uniforme (29) e desfaz. As posições dos spans já vinham corretas, então os
   recortes não são afetados.
3. **PDFs sem camada de texto** — 11 documentos, incluindo a prova de 2026
   inteira, são digitalizações. Quando a detecção normal não acha nada, o batch
   reprocessa com OCR a 300 DPI. O OCR é preferível a um modelo de visão aqui
   porque devolve texto **com coordenadas**, que é o que o recorte precisa.

`data/processed/coverage_report.json` registra o que não foi detectado e por quê.
`task coverage` resume.

### 3. Enrich — `src/etl/enrich/` (opcional)

Manda o recorte PNG e o texto bruto para o Claude e recebe o enunciado em
Markdown/LaTeX, as alternativas, a matéria e os assuntos.

Existe porque a extração de texto destrói a matemática: `x «6 „ x −2 x «5` é o
que o PyMuPDF devolve para uma expressão perfeitamente legível na imagem. As
figuras são vetoriais (TikZ), então só a renderização as preserva.

- Cache em disco em `data/cache/enrich/`, chaveado por conteúdo + versão do
  prompt + modelo. Reexecutar não gasta nada.
- `--dry-run` conta tokens de verdade e estima o custo antes de qualquer gasto.
- `--limit`, `--only`, `--max-cost`, `--retry-failed`.
- Cada resultado é gravado no instante em que chega: uma queda no meio do lote
  custa uma questão, não o lote.

O LLM **nunca** sobrescreve os campos `*_raw`. Ele preenche as colunas Markdown e
marca a linha como `llm_generated`, deixando a extração original auditável.

### 4. Load — `src/etl/load/`

Lê os manifests e carrega no SQLite com upsert por chave natural. Projeta o
gabarito nas alternativas, completa matérias faltantes e monta o `search_blob`
que alimenta o índice FTS5.

Termina com `VACUUM` e `journal_mode=DELETE`, para que o `.db` seja um arquivo
único que a API possa montar somente-leitura.

## API

```bash
task api          # docker
# ou
pipenv run uvicorn src.main:app --reload
```

Documentação interativa em `/docs`.

| Endpoint | |
|---|---|
| `GET /api/v1/questions` | filtros por `year`, `test_type`, `phase`, `discipline`, `topic`, `format_type`, `has_figure`, `has_answer`, `review_status` e busca full-text em `q`. Paginação por `page` e `page_size`; o envelope traz `{items, total, page, page_size, pages}` |
| `GET /api/v1/questions/random` | uma questão aleatória, com os mesmos filtros |
| `GET /api/v1/questions/{id}` | detalhe com alternativas, páginas e fonte |
| `GET /api/v1/questions/{id}/image` | o recorte em PNG |
| `GET /api/v1/exams` | provas com contagem de questões |
| `GET /api/v1/stats` | cobertura por ano, matéria e status de revisão |

```bash
curl "localhost:8000/api/v1/questions?discipline=matematica&q=numero+complexo&order=relevance"
```

A busca usa FTS5 com `remove_diacritics`, então `funcao` encontra `função`.

## Interface de auditoria

```bash
task review          # http://localhost:8000/review
```

Três painéis: documentos → questões → detalhe. A lista de documentos filtra por
tipo, ano, fase, matéria e prova/gabarito, e o topo mostra o progresso global e
o do documento atual numa linha só. `/review/progresso` abre o painel detalhado,
com o corte por tipo, matéria, fase e ano, mais a lista de documentos ainda não
auditados ordenada por tamanho — a resposta para "por onde continuar".

O detalhe põe **o recorte e o texto bruto lado a lado**, e os campos de
transcrição logo abaixo. Mais adiante na página fica a imagem da página inteira
com as regiões detectadas desenhadas por cima (azul = a questão selecionada,
vermelho = as demais). Gabaritos abrem num painel próprio, com as respostas
parseadas coluna a coluna ao lado do PDF.

Sete tipos de intervenção, todos persistidos:

| Ação | Efeito |
|---|---|
| **Aprovar / rejeitar** | Define `review_status`. `human_verified` blinda a linha contra o upsert e contra o LLM. |
| **Precisa / dispensa LLM** | Override da triagem automática, por questão. |
| **Corrigir o texto** | Edita enunciado e alternativas em Markdown com LaTeX. Salvar já marca como revisada e dispensa o LLM — consertar à mão é o caminho mais barato. |
| **Ajustar o recorte** | Arraste sobre a página. A seleção fica marcada e é ajustável pelas oito alças até você aplicar; o PNG é regerado e o texto reextraído do novo retângulo. |
| **Criar questão** | Marque a região e crie. Serve para o que a detecção não pegou; nasce `human_verified`. |
| **Excluir questão** | Remove o que a detecção inventou. |
| **Renumerar** | Muda o número; se o destino existir, as duas trocam. |
| **Anexar figura** | Associa à questão uma imagem impressa longe do enunciado. |

**Auto-preencher.** O botão *Preencher do texto bruto* separa enunciado e
alternativas sem chamar LLM — os marcadores foram medidos no corpus (`A ( )`
cobre 54%, e as alternativas curtas vêm todas numa linha só). Fecha as cinco em
58% das questões com texto. É uma sugestão: só preenche campo vazio, e uma
questão discursiva com itens a)–e) é indistinguível de uma objetiva pelo texto,
então confira contra o recorte ao lado antes de salvar.

**Figuras soltas.** Nas provas de pós a figura costuma ficar numa página adiante
do enunciado. O card *Figuras da questão* lista as regiões que ficaram fora de
qualquer questão — com preview, calculadas ao vivo a partir dos retângulos das
questões — e um clique anexa a escolhida. Também dá para marcar a região à mão em
qualquer página e usar *＋ Anexar figura*.

A figura entra como `AssetKind.FIGURE`, **não** como parte da questão: o
retângulo do enunciado e as páginas dele não se movem, então anexar uma imagem da
página 9 a uma questão da página 3 não corrompe nada. A associação é registrada e
reaplicada no `--rebuild`.

**Equações.** O enunciado aceita `$x^2$` inline e `$$...$$` em bloco, com
preview ao vivo ao lado e uma barra de atalhos para os símbolos que aparecem nas
provas. A renderização usa [temml](https://temml.org), vendorizado: converte
LaTeX em MathML e deixa o navegador desenhar, o que custa um arquivo de 240 KB
em vez do ~1 MB de fontes que o KaTeX exigiria. `![figura](caminho)` também
funciona.

| Tecla | |
|---|---|
| `↑` `↓` (ou `j` `k`) | questão anterior / seguinte |
| `←` `→` | documento anterior / seguinte |
| `A` / `R` | aprovar / rejeitar |
| `L` / `D` | marcar que precisa / dispensa LLM |

A revisão é gravada em duas camadas: no banco, para a API refletir na hora, e
em arquivos versionados que o load reaplica — o que faz a auditoria sobreviver
a um `task db-rebuild`, que apaga o `.db` inteiro:

| Arquivo | |
|---|---|
| `data/review/decisions.json` | Estado final por questão, mais a lista ordenada de criar/excluir/renumerar. É o que o load aplica. |
| `data/review/audit.jsonl` | Trilha append-only: cada ação, com o antes e o depois. |
| `data/review/baseline.json` | Contagens esperadas e o ambiente que as produziu. |

São o único conteúdo de `data/` versionado no git — tudo o mais o pipeline
regenera, a revisão humana não. Procedimento completo em
[REPRODUCIBILITY.md](REPRODUCIBILITY.md).

React e htm estão vendorizados em `src/static/vendor/` (~146 KB). Não há npm,
não há build, e a interface abre sem internet.

> A escrita da revisão exige o banco montado para escrita. Funciona com
> `task review` local; **não** funciona no container, onde o `.db` é montado
> `:ro` de propósito.

## Modelo de dados

`exams` (ano + fase) é separado de `documents` (um por PDF): a 1ª fase é um PDF
com cinco matérias, a 2ª fase são PDFs por matéria, e os gabaritos são arquivos
próprios. A chave natural da questão é `(document_id, number)` — em 2008–2018
cada matéria recomeça a numeração em 1.

Três sinais independentes registram a procedência de cada linha:

- `statement_markdown IS NULL` — nunca passou pelo LLM
- `review_status = 'llm_generated'` — o LLM produziu, ninguém revisou
- `review_status = 'human_verified'` — revisado, e o ETL não sobrescreve

`discipline_source` registra de onde veio a matéria: nome do arquivo, cabeçalho
de seção, gabarito ou LLM, nessa ordem de confiança.

Modelos em `src/models/`, um arquivo por tabela. Novos modelos precisam ser
importados em `src/models/__init__.py` para o Alembic enxergar.

```bash
task makemigrations -- "mensagem"
task migrate
```

## Reprodutibilidade

```bash
task verify                  # confere o estado contra o baseline versionado
task verify -- --snapshot    # redefine o baseline (ato deliberado)
```

O `verify` compara contagens estruturais e, se divergirem, aponta exatamente
quais e o que mudou no ambiente (versões de PyMuPDF, Tesseract, git sha). O
procedimento passo a passo para reconstruir do zero está em
[REPRODUCIBILITY.md](REPRODUCIBILITY.md).

### O banner

```bash
task banner                       # redesenha docs/banner-{light,dark}.svg
task banner -- --check            # falha se o SVG em disco discordar do baseline
task banner -- --preview-emblema  # imprime o mosaico do emblema em texto
```

`tools/banner.py` desenha o banner do topo com a stdlib e nada mais: um quadrado
por questão, agrupados em torres por ano, com a divisão por matéria embaixo.

As duas entradas são versionadas: `data/review/baseline.json` — o mesmo arquivo
do `verify`, e não o `.db` — e `tools/assets/ita-logo.png`. Não há `random`, nem
timestamp, nem leitura de rede, então dois clones do repositório produzem SVGs
byte a byte idênticos e o banner só aparece no `git diff` quando o corpus
realmente muda. O `--check` é o que impede o gráfico de mentir depois de um
`task load`.

O emblema à esquerda é um **mosaico da mesma grade dos dados**, e não a imagem
embutida: o PNG é decodificado com `zlib` puro — o passo do CI roda num job sem
PyMuPDF nem Pillow — e reduzido a 64×24 células, cada uma pintada com a cor que
domina os pixels dela. Asas em ouro, escudo e letras em azul, tocha em vermelho,
nas cores amostradas do próprio arquivo.

A classificação é por **matiz**, não por luminância: azul (~99) e vermelho (~72)
têm luminância parecida, então um limiar de brilho funde escudo e tocha na mesma
mancha — foi a primeira versão, e ela perdia o desenho. O contorno preto só
vence quando a célula não tem nenhuma das três cores, senão o traço escuro come
as áreas de cor nessa resolução. Os dois temas têm pigmento próprio: `#ffcb05`
sobre branco dá 1,47:1 e as asas somem, então em light o ouro desce para
`#c08a00`; em dark é o azul que sobe, porque `#0066b3` contra `#1a1a19` fica em
2,98:1 e o escudo apagava.

Os dois temas existem porque o `<picture>` do GitHub troca de arquivo conforme o
tema do leitor; o escuro não é o claro invertido — cada um tem seus próprios
passos da rampa, escolhidos contra o respectivo fundo.

> O emblema é marca do ITA, reproduzido aqui como referência à origem das provas.
> Este é um projeto independente, **sem vínculo, endosso ou afiliação** com o
> Instituto Tecnológico de Aeronáutica.

## Testes

```bash
PYTHONPATH=. pipenv run pytest tests/ -q
task check                   # ruff
task format                  # ruff format
```

Os testes constroem PDFs sintéticos e bancos temporários — não tocam no corpus
versionado, então nenhum deles depende de `task transform` ter rodado.

## Limitações conhecidas

- **Sem Tesseract, 11 documentos ficam sem detecção** — o pipeline não quebra,
  mas o relatório de cobertura os lista com a instrução de instalação.
- **O texto de OCR tem erros de reconhecimento.** `p(x) = x) + bx? + cx + d`
  deveria ser `x³ + bx² + cx + d`. O recorte em PNG está correto; é o texto que
  degrada. Essas questões são candidatas prioritárias ao estágio de enrich, que
  lê da imagem.
- **O scraper depende de XPaths** de um site com `<frame>` de 2008 e vai quebrar
  no primeiro redesenho. Isso deixou de ameaçar o corpus — ele está versionado,
  com o sha256 de origem — mas ameaça sim a busca de um ano novo.
- **Provas de pós não têm gabarito** publicado, então suas questões ficam sem
  resposta conhecida.
- **Os PDFs versionados pesam no clone.** São 108 MB que não deltificam; um
  `git clone --filter=blob:none` busca os blobs sob demanda.
- **`data/processed/` e o `.db` são gitignored** — 400 MB de derivados que
  `task transform && task load` refazem. O que não é regenerável está no git.
