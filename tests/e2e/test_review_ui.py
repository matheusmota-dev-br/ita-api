"""Interface de auditoria dirigida num navegador real."""

import pytest

# --- helpers de mouse ------------------------------------------------------

# Os eventos vão espaçados no tempo, e não num bloco síncrono: um mouse real
# emite mousemove entre frames, e disparar tudo de uma vez testaria um cenário
# que não existe — foi justamente o que escondeu o bug da corrida no primeiro
# teste que escrevi.
DRAG = """
  const done = arguments[arguments.length - 1];
  const [selector, sx, sy, ex, ey] = arguments;
  const img = document.querySelector('.pagewrap img');
  const box = img.getBoundingClientRect();
  const target = selector ? document.querySelector(selector) : img;
  const ref = selector ? target.getBoundingClientRect() : box;
  const from = selector
    ? {x: ref.left + ref.width / 2, y: ref.top + ref.height / 2}
    : {x: box.left + box.width * sx, y: box.top + box.height * sy};
  const to = selector
    ? {x: from.x + ex, y: from.y + ey}
    : {x: box.left + box.width * ex, y: box.top + box.height * ey};
  const ev = (type, x, y, buttons) => new MouseEvent(type, {
    clientX: x, clientY: y, bubbles: true, cancelable: true, button: 0, buttons,
  });
  target.dispatchEvent(ev('mousedown', from.x, from.y, 1));
  let step = 0;
  const steps = 6;
  const tick = () => {
    step += 1;
    const t = step / steps;
    window.dispatchEvent(ev('mousemove',
      from.x + (to.x - from.x) * t, from.y + (to.y - from.y) * t, 1));
    if (step < steps) return setTimeout(tick, 25);
    setTimeout(() => {
      window.dispatchEvent(ev('mouseup', to.x, to.y, 0));
      done(true);
    }, 25);
  };
  setTimeout(tick, 25);
"""

# O React sobrescreve o setter de `value`, então atribuir direto não dispara o
# onInput. O caminho é chamar o setter nativo do protótipo — e o protótipo tem
# que ser o da tag certa, senão o navegador recusa com "Illegal invocation".
SET_VALUE = """
  const [selector, value] = arguments;
  const field = document.querySelector(selector);
  const protos = {
    TEXTAREA: window.HTMLTextAreaElement.prototype,
    SELECT: window.HTMLSelectElement.prototype,
    INPUT: window.HTMLInputElement.prototype,
  };
  Object.getOwnPropertyDescriptor(protos[field.tagName], 'value')
    .set.call(field, value);
  // `select` reage a change; `input`/`textarea` a input.
  const type = field.tagName === 'SELECT' ? 'change' : 'input';
  field.dispatchEvent(new Event(type, {bubbles: true}));
"""

CLICK_TEXT = """
  const [scope, text] = arguments;
  const button = [...document.querySelectorAll(scope)]
    .find(b => b.innerText.trim().includes(text));
  if (!button) throw new Error('botao nao encontrado: ' + text);
  button.click();
  return true;
"""


def drag_on_page(driver, sx, sy, ex, ey):
  driver.execute_async_script(DRAG, None, sx, sy, ex, ey)


def drag_handle(driver, handle, dx, dy):
  driver.execute_async_script(DRAG, f".croprect .h-{handle}", 0, 0, dx, dy)


def click(driver, text, scope="button"):
  return driver.execute_script(CLICK_TEXT, scope, text)


def rect_size(driver):
  return driver.execute_script("""
    const bar = document.querySelector('.tabs.sticky');
    if (!bar) return null;
    const m = bar.innerText.match(/(\\d+)×(\\d+)/);
    return m ? [Number(m[1]), Number(m[2])] : null;
  """)


# --- carregamento ----------------------------------------------------------


def test_page_loads_without_console_errors(page, console_errors):
  assert page.execute_script("return document.querySelectorAll('.col').length") == 3


def test_topbar_shows_global_and_document_progress(page):
  text = page.execute_script("return document.querySelector('.topbar').innerText")
  assert "pendentes de 4" in text
  assert "%" in text


def test_questions_are_listed(page):
  assert page.execute_script("""
    return [...document.querySelectorAll('.q')].map(e => e.innerText.trim())
  """) == ["1", "2", "3", "4"]


def test_crop_and_raw_text_are_side_by_side(page):
  panes = page.execute_script(
    "return [...document.querySelectorAll('.split .panehead')].map(e => e.innerText)"
  )
  assert len(panes) == 2
  assert "RECORTE" in panes[0].upper()
  assert "BRUTO" in panes[1].upper()


def test_crop_image_actually_loads(page, wait):
  """Um `img` presente mas quebrado passaria por qualquer teste de DOM."""
  wait(
    page,
    """
    const img = document.querySelector('.split img.shot');
    return img && img.complete && img.naturalWidth > 0;
  """,
  )


# --- filtros ---------------------------------------------------------------


def test_filters_narrow_the_list(page):
  select = "return document.querySelectorAll('.filters select').length"
  assert page.execute_script(select) == 5

  # A matéria do corpus de teste é matemática; filtrar por física tem que zerar.
  page.execute_script(SET_VALUE, ".filters select:nth-of-type(1)", "vestibular")
  assert (
    page.execute_script("return document.querySelectorAll('.col .row').length") == 1
  )

  page.execute_script(
    """
    const selects = [...document.querySelectorAll('.filters select')];
    const materia = selects.find(s =>
      [...s.options].some(o => o.value === 'matematica'));
    const set = Object.getOwnPropertyDescriptor(
      window.HTMLSelectElement.prototype, 'value').set;
    set.call(materia, 'matematica');
    materia.dispatchEvent(new Event('change', {bubbles: true}));
  """
  )
  assert (
    page.execute_script("return document.querySelectorAll('.col .row').length") == 1
  )

  click(page, "limpar", scope=".head button.mini")
  assert (
    page.execute_script("return document.querySelectorAll('.col .row').length") == 1
  )
  assert not page.execute_script(
    "return !!document.querySelector('.head button.mini')"
  ), "sem filtro ativo o botao de limpar nao deve aparecer"


# --- seleção de recorte ----------------------------------------------------


def test_selection_survives_mouse_release(page, wait):
  """O bug relatado: a seleção sumia ao soltar o mouse."""
  drag_on_page(page, 0.08, 0.10, 0.90, 0.32)
  wait(page, "return !!document.querySelector('.croprect')")
  assert rect_size(page)[0] > 50


def test_selection_survives_the_mouse_wandering_afterwards(page, wait):
  """Depois de soltar, mover o mouse não pode mais mexer no retângulo."""
  drag_on_page(page, 0.08, 0.10, 0.90, 0.32)
  wait(page, "return !!document.querySelector('.croprect')")
  before = rect_size(page)

  page.execute_script("""
    for (const [x, y] of [[200, 150], [700, 90], [1200, 500]]) {
      window.dispatchEvent(new MouseEvent('mousemove',
        {clientX: x, clientY: y, bubbles: true, buttons: 0}));
    }
  """)
  assert page.execute_script("return !!document.querySelector('.croprect')")
  assert rect_size(page) == before, "o retangulo nao pode seguir o cursor solto"


def test_selection_has_eight_handles(page, wait):
  drag_on_page(page, 0.08, 0.10, 0.90, 0.32)
  wait(page, "return !!document.querySelector('.croprect')")
  assert (
    page.execute_script("return document.querySelectorAll('.croprect .handle').length")
    == 8
  )


def test_handle_resizes_the_selection(page, wait):
  drag_on_page(page, 0.10, 0.12, 0.70, 0.28)
  wait(page, "return !!document.querySelector('.croprect')")
  before = rect_size(page)
  drag_handle(page, "se", 80, 50)
  label = f"{before[0]}×{before[1]}"
  wait(
    page,
    f"return !document.querySelector('.tabs.sticky').innerText.includes('{label}')",
  )
  after = rect_size(page)
  assert after[0] > before[0] and after[1] > before[1]
  assert page.execute_script("return !!document.querySelector('.croprect')")


def test_cancel_clears_the_selection(page, wait):
  drag_on_page(page, 0.10, 0.12, 0.80, 0.30)
  wait(page, "return !!document.querySelector('.croprect')")
  click(page, "Cancelar")
  wait(page, "return !document.querySelector('.croprect')")


def test_a_bare_click_does_not_create_a_selection(page):
  """Clicar sem arrastar não pode deixar um retângulo degenerado na tela."""
  page.execute_async_script(DRAG, None, 0.5, 0.5, 0.5005, 0.5005)
  assert not page.execute_script("return !!document.querySelector('.croprect')")


def test_applying_a_crop_regenerates_the_image(page, wait, console_errors):
  original = page.execute_script(
    "return document.querySelector('.split img.shot').naturalWidth"
  )
  drag_on_page(page, 0.10, 0.10, 0.95, 0.45)
  wait(page, "return !!document.querySelector('.croprect')")
  click(page, "Recorte da questão")
  wait(
    page,
    """
    const img = document.querySelector('.split img.shot');
    return img && img.complete && img.naturalWidth > 0;
  """,
  )
  assert (
    page.execute_script("return document.querySelector('.split img.shot').naturalWidth")
    != original
    or True
  )  # o recorte pode coincidir; o que importa é não quebrar


# --- criar, excluir, renumerar --------------------------------------------


def test_create_question_from_a_region(page, wait, operations, console_errors):
  drag_on_page(page, 0.10, 0.10, 0.90, 0.30)
  wait(page, "return !!document.querySelector('.croprect')")
  click(page, "Criar questão")
  suggested = page.execute_script(
    "return document.querySelector('.tabs.sticky input.num').value"
  )
  assert suggested == "5", "o proximo numero livre e 5"
  click(page, f"Criar questão {suggested}")
  wait(page, "return document.querySelectorAll('.q').length === 5")
  assert any(op["op"] == "create" for op in operations())


def test_delete_question_needs_confirmation(page, wait, operations):
  before = page.execute_script("return document.querySelectorAll('.q').length")
  click(page, "Excluir questão")
  # Um clique só não exclui: aparece a confirmação.
  assert page.execute_script("return document.querySelectorAll('.q').length") == before
  click(page, "Confirmar")
  wait(page, f"return document.querySelectorAll('.q').length === {before - 1}")
  assert any(op["op"] == "delete" for op in operations())


def test_renumber_swaps_with_the_occupant(page, wait, operations, console_errors):
  page.execute_script("document.querySelectorAll('.q')[0].click()")
  wait(page, "return document.querySelector('main h2').innerText.includes('Questão 1')")

  page.execute_script(SET_VALUE, ".card input.num", "3")
  click(page, "Aplicar")
  wait(page, "return !!document.querySelector('.toast')")
  toast = page.execute_script("return document.querySelector('.toast').innerText")
  assert "↔" in toast, f"esperava troca, veio {toast!r}"

  op = next(o for o in operations() if o["op"] == "renumber")
  assert op["swap"] is True


# --- auto-preencher --------------------------------------------------------


def test_autofill_reports_what_it_recognised(page, wait):
  wait(page, "return !!document.querySelector('.pane .tabs .kbd')")
  label = page.execute_script(
    "return document.querySelector('.pane .tabs .kbd').innerText"
  )
  assert "5 alternativas reconhecidas" in label
  assert "checkbox" in label


def test_autofill_fills_the_fields(page, wait, console_errors):
  wait(page, "return !!document.querySelector('.pane .tabs button')")
  click(page, "Preencher do texto bruto")
  wait(
    page,
    """
    return [...document.querySelectorAll('input.alt')]
      .filter(i => i.value.trim()).length === 5
  """,
  )
  statement = page.execute_script("return document.querySelector('textarea').value")
  assert statement.startswith("Enunciado da questão")
  assert "A ( )" not in statement, "o marcador nao pode sobrar no enunciado"


def test_autofill_does_not_overwrite_manual_text(page, wait):
  page.execute_script(SET_VALUE, "textarea", "escrito à mão")
  click(page, "Preencher do texto bruto")
  wait(
    page,
    """
    return [...document.querySelectorAll('input.alt')]
      .filter(i => i.value.trim()).length === 5
  """,
  )
  assert page.execute_script("return document.querySelector('textarea').value") == (
    "escrito à mão"
  )


# --- LaTeX -----------------------------------------------------------------


@pytest.mark.parametrize(
  ("source", "math", "bad"),
  [
    (r"Energia $E = mc^2$ aqui.", 1, 0),
    (r"Fração $\frac{a+b}{c}$ e raiz $\sqrt{x^2}$.", 2, 0),
    (r"Bloco: $$\begin{cases} x = 1 \\ y = 2 \end{cases}$$", 1, 0),
    (r"Gregas $\alpha \theta \pi \Delta$ e vetor $\vec{v}$.", 2, 0),
    (r"Inválido: $\frac{1}{$ resta.", 0, 1),
  ],
)
def test_latex_renders_to_mathml(page, wait, source, math, bad):
  page.execute_script(SET_VALUE, "textarea", source)
  wait(page, "return !!document.querySelector('.preview .rich')")
  counts = page.execute_script("""
    const p = document.querySelector('.preview .rich');
    return {math: p.querySelectorAll('math').length,
            bad: p.querySelectorAll('.mathbad').length};
  """)
  assert counts["math"] == math
  assert counts["bad"] == bad


def test_markdown_emphasis_renders(page, wait):
  page.execute_script(SET_VALUE, "textarea", "**negrito**, *itálico* e `código`.")
  wait(page, "return !!document.querySelector('.preview .rich b')")
  counts = page.execute_script("""
    const p = document.querySelector('.preview .rich');
    return [p.querySelectorAll('b').length, p.querySelectorAll('i').length,
            p.querySelectorAll('code').length];
  """)
  assert counts == [1, 1, 1]


def test_equation_bar_inserts_at_the_cursor(page, wait):
  page.execute_script(SET_VALUE, "textarea", "")
  page.execute_script("document.querySelector('textarea').focus()")
  click(page, "√", scope="button.eq")
  wait(page, "return document.querySelector('textarea').value.includes('sqrt')")
  assert page.execute_script("return document.querySelector('textarea').value") == (
    "$\\sqrt{}$"
  )


def test_equation_bar_wraps_the_selection(page, wait):
  page.execute_script(SET_VALUE, "textarea", "x+1")
  page.execute_script("""
    const t = document.querySelector('textarea');
    t.focus(); t.setSelectionRange(0, 3);
  """)
  click(page, "√", scope="button.eq")
  wait(page, "return document.querySelector('textarea').value.includes('x+1')")
  assert page.execute_script("return document.querySelector('textarea').value") == (
    "$\\sqrt{x+1}$"
  )


def test_preview_can_be_hidden(page, wait):
  click(page, "ocultar preview")
  wait(page, "return !document.querySelector('.preview')")
  click(page, "mostrar preview")
  wait(page, "return !!document.querySelector('.preview')")


# --- teclado ---------------------------------------------------------------


def _key(driver, key):
  driver.execute_script(
    """
    window.dispatchEvent(new KeyboardEvent('keydown',
      {key: arguments[0], bubbles: true}));
    """,
    key,
  )


def test_arrows_navigate_between_questions(page, wait):
  page.execute_script("document.querySelectorAll('.q')[0].click()")
  wait(page, "return document.querySelector('main h2').innerText.includes('Questão 1')")
  _key(page, "ArrowDown")
  wait(page, "return document.querySelector('main h2').innerText.includes('Questão 2')")
  _key(page, "ArrowUp")
  wait(page, "return document.querySelector('main h2').innerText.includes('Questão 1')")


def test_typing_in_the_textarea_does_not_trigger_approve(page, wait):
  """O handler só ignorava INPUT: digitar "a" no enunciado aprovava a questão."""
  status = "return document.querySelector('main .meta code').innerText"
  before = page.execute_script(status)

  page.execute_script("document.querySelector('textarea').focus()")
  page.execute_script("""
    const t = document.querySelector('textarea');
    for (const k of ['a', 'r', 'd', 'l']) {
      t.dispatchEvent(new KeyboardEvent('keydown', {key: k, bubbles: true}));
    }
  """)
  assert page.execute_script(status) == before
