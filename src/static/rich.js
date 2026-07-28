/**
 * Texto rico da transcrição: LaTeX, ênfase e imagens.
 *
 * O enunciado de uma prova do ITA é texto com matemática no meio, e às vezes
 * uma figura. Guardar isso como Markdown com `$...$` mantém o campo legível e
 * diffável — o que importa para uma auditoria — e deixa a renderização ser um
 * detalhe da interface.
 *
 * A renderização usa temml, que converte LaTeX em MathML e deixa o navegador
 * desenhar. Foi escolhido no lugar do KaTeX porque o KaTeX precisa de ~1 MB de
 * fontes próprias, e a exigência aqui é funcionar offline, sem instalar nada.
 * MathML nativo custa um arquivo de 240 KB e nenhuma fonte.
 */

import temml from "/static/vendor/temml.js";

const ESCAPE = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => ESCAPE[c]);

// `$$...$$` antes de `$...$`, senão o delimitador duplo é lido como dois
// simples com nada dentro. `\$` escapa um cifrão literal.
const SEGMENT = /(\$\$[\s\S]+?\$\$|(?<!\\)\$[^$\n]+?(?<!\\)\$)/g;

/** Markdown mínimo: o suficiente para um enunciado, nada além. */
function inlineMarkup(text) {
  return (
    escapeHtml(text)
      // Imagem antes de link: a sintaxe só difere pelo "!" inicial.
      .replace(
        /!\[([^\]]*)\]\(([^)\s]+)\)/g,
        (_, alt, src) => `<img class="inline-fig" src="${src}" alt="${alt}">`
      )
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, "<i>$1</i>")
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\n/g, "<br>")
  );
}

/**
 * Converte o texto da transcrição em HTML.
 *
 * Uma fórmula inválida vira um marcador vermelho com a mensagem do temml, em
 * vez de derrubar o preview inteiro: enquanto se digita, o texto passa a maior
 * parte do tempo num estado sintaticamente incompleto.
 */
export function renderRich(text) {
  if (!text) return "";
  return String(text)
    .split(SEGMENT)
    .map((part) => {
      const display = part.startsWith("$$") && part.endsWith("$$");
      const inline = !display && part.startsWith("$") && part.endsWith("$");
      if (!display && !inline) return inlineMarkup(part);

      const latex = display ? part.slice(2, -2) : part.slice(1, -1);
      try {
        return temml.renderToString(latex, { displayMode: display, throwOnError: true });
      } catch (err) {
        return `<span class="mathbad" title="${escapeHtml(err.message || "")}">${escapeHtml(
          part
        )}</span>`;
      }
    })
    .join("");
}

/** Insere `snippet` na posição do cursor, devolvendo o novo texto e o cursor. */
export function insertAtCursor(field, snippet) {
  const start = field.selectionStart ?? field.value.length;
  const end = field.selectionEnd ?? start;
  const selected = field.value.slice(start, end);
  // `@` marca onde o cursor deve parar; a seleção existente entra no lugar
  // dele, para que envolver algo já escrito funcione.
  const body = snippet.replace("@", selected);
  const caret = snippet.indexOf("@");
  return {
    value: field.value.slice(0, start) + body + field.value.slice(end),
    caret: start + (caret === -1 ? body.length : caret + selected.length),
  };
}

/**
 * Atalhos da barra de equações.
 *
 * Escolhidos pelo que aparece nas provas do ITA — fração, raiz, potência,
 * índice, somatório, integral, limite, vetor e as letras gregas usadas em
 * física — e não pela lista completa do LaTeX, que não caberia na tela.
 */
export const SNIPPETS = [
  { label: "x²", latex: "$@^{2}$", title: "potência" },
  { label: "xᵢ", latex: "$@_{i}$", title: "índice" },
  { label: "a/b", latex: "$\\frac{@}{b}$", title: "fração" },
  { label: "√", latex: "$\\sqrt{@}$", title: "raiz" },
  { label: "ⁿ√", latex: "$\\sqrt[n]{@}$", title: "raiz n-ésima" },
  { label: "∑", latex: "$\\sum_{i=1}^{n} @$", title: "somatório" },
  { label: "∫", latex: "$\\int_{a}^{b} @ \\, dx$", title: "integral" },
  { label: "lim", latex: "$\\lim_{x \\to @} $", title: "limite" },
  { label: "≤", latex: "$\\leq$", title: "menor ou igual" },
  { label: "≥", latex: "$\\geq$", title: "maior ou igual" },
  { label: "≠", latex: "$\\neq$", title: "diferente" },
  { label: "±", latex: "$\\pm$", title: "mais ou menos" },
  { label: "×", latex: "$\\times$", title: "vezes" },
  { label: "·10ⁿ", latex: "$\\cdot 10^{@}$", title: "notação científica" },
  { label: "α", latex: "$\\alpha$", title: "alfa" },
  { label: "θ", latex: "$\\theta$", title: "teta" },
  { label: "π", latex: "$\\pi$", title: "pi" },
  { label: "Δ", latex: "$\\Delta$", title: "delta" },
  { label: "ω", latex: "$\\omega$", title: "ômega" },
  { label: "→", latex: "$\\rightarrow$", title: "seta" },
  { label: "v⃗", latex: "$\\vec{@}$", title: "vetor" },
  { label: "matriz", latex: "$$\\begin{pmatrix} @ & b \\\\ c & d \\end{pmatrix}$$", title: "matriz" },
  { label: "sistema", latex: "$$\\begin{cases} @ \\\\ y = 2 \\end{cases}$$", title: "sistema" },
  { label: "figura", latex: "![descrição](@)", title: "imagem" },
];
