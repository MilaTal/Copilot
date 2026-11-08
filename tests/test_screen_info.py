"""Экран: пояснения — под значком «i», на экране только то, что нужно для действия (решение владелицы 05.11.2026).

Поведение в браузере (нажатие, касание, фокус, Escape) проверяется прогоном экрана; здесь — что разметка экрана
не вернула прежние открытые пояснения и что значок собран с подписями для экранного чтения."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")


def test_explanations_not_rendered_openly():
    """Прежде подсказка, «зачем», пример и правила ввода стояли открытым текстом рядом с вопросом и полем."""
    for cls in ("wk-hint", "qx-hint", "q-why", "qx-quiet", "wk-help", "wk-comp-note", "lead-q", "sx-goal", "sx-lead"):
        assert f'class="{cls}' not in JS, cls
    for text in ("Зачем Senior Pilot:", "Как ответить и пример из практики", "Пример и наводящие вопросы",
                 "Здесь будет разговор по текущему шагу", "Можно написать «не знаю», «опускаю» или «варианты»"):
        assert text not in JS, text
    assert "Спросите о текущем шаге" not in HTML  # пояснение панели Senior Pilot — под «i» у заголовка


def test_question_explanation_lives_under_info():
    """Всё о вопросе — зачем, как ответить, наводящие вопросы, пример, зачем в проде — собирается в одно «i»."""
    body = JS[JS.index("function qExplain"):JS.index("function prodNote")]
    for field in ("q.why", "q.hint", "q.guide", "q.example", "q.no_sample", "q.mentor_note"):
        assert field in body, field
    # «i» у вопроса во всех четырёх видах: рабочее место, лента и карточки, классика
    assert JS.count('info(qExplain(q, rec), "Подсказка к вопросу")') == 3


def test_info_button_accessible_and_not_on_hover():
    btn = JS[JS.index("function info("):JS.index("function infoInline")]
    for attr in ('type="button"', "aria-expanded=\"false\"", "aria-controls=", "aria-label=", "hidden"):
        assert attr in btn, attr


def test_hint_marker_is_recognisable():
    """Владелица 05.11.2026: значок «i» непонятен — подсказка видна сразу: лампа и слово «Подсказка».
    Прежде кнопка показывала одну букву «i»; теперь так нельзя."""
    btn = JS[JS.index("function info("):JS.index("function infoInline")]
    assert ">i</button>" not in JS
    assert "${LAMP}" in btn and '<span class="info-t">Подсказка</span>' in btn
    assert '"Пояснение' not in JS  # подписи для экранного чтения тоже говорят «Подсказка»
    handlers = JS[JS.index("function bindInfoOnce"):JS.index("function headInfo")]
    assert '"Escape"' in handlers and ":focus-visible" in handlers and '"click"' in handlers
    assert not re.search(r"mouseenter|mouseover|pointerenter|:hover", handlers)  # на телефоне — нажатием, не наведением


def test_prod_note_repeats_not_shown_twice():
    """Каждый факт — один раз: предложения «Зачем это в проде», совпадающие с «Зачем», не повторяются."""
    assert "function prodNote" in JS and "prodNote(q)" in JS[JS.index("function qExplain"):]
