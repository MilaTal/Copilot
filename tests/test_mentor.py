"""Модель диалога наставника: лестница поддержки, бюджет и утомление, формулировка ответа, вывод шага,
предзаполнение таблиц, переезд сохранённых проектов."""
import json

import pytest

from app import core, mentor, senior, state


class Says:
    """Подставная модель: возвращает заданный JSON и запоминает промпт."""

    def __init__(self, data):
        self.data, self.prompts = data, []

    def chat(self, role, system, messages, **kw):
        self.prompts.append(messages[-1]["content"])
        return type("R", (), {"text": json.dumps(self.data, ensure_ascii=False), "cost_usd": 0.0, "model": "fake",
                              "truncated": False, "tokens_out": 10})()


NEED = {"verdict": "needs_detail", "comment": "Есть.", "follow_up": "Что приходит на вход?",
        "follow_options": ["письмо", "звонок", "документ", "четвёртый лишний"], "sample": "Приходит [что], выходит [что].",
        "synthesis": "", "risks": []}


def _answer(st, step, qid, text="Проверять договоры"):
    state.set_answer(st, step, qid, "answered", text)


def test_ladder_climbs_and_never_goes_back():
    st = state.create("Лестница", 2)
    q = "write_action"
    stages = []
    for _ in range(3):
        _answer(st, 2, q, "Агент меняет запись в системе")
        rv = senior.review_answer(st, 2, q, router=Says(NEED))
        stages.append(rv["stage"])
        mentor.record(st, 2, q, rv)
    assert stages == [1, 2, 3]
    assert senior.review_answer(st, 2, q, router=None)["stage"] == 4  # четвёртый раз — без модели


def test_stage_controls_what_is_shown():
    st = state.create("Ступени", 2)
    _answer(st, 2, "write_action", "Агент меняет запись в системе")
    rv1 = senior.review_answer(st, 2, "write_action", router=Says(NEED))
    assert rv1["follow_up"] and rv1["follow_options"] == [] and rv1["sample"] == ""
    mentor.record(st, 2, "write_action", rv1)
    rv2 = senior.review_answer(st, 2, "write_action", router=Says(NEED))
    assert len(rv2["follow_options"]) == 3 and rv2["sample"] == ""
    mentor.record(st, 2, "write_action", rv2)
    rv3 = senior.review_answer(st, 2, "write_action", router=Says(NEED))
    assert rv3["sample"].startswith("Приходит")


def test_no_sample_questions_get_options_not_sample():
    qs = [q for s in core.steps() for q in s["questions"] if q.get("no_sample")]
    if not qs:
        pytest.skip("в ядре нет вопросов-решений с no_sample")
    sid = next(s["id"] for s in core.steps() for q in s["questions"] if q["id"] == qs[0]["id"])
    st = state.create("Решение", sid)
    st["mentor"] = {f"{sid}:{qs[0]['id']}": {"guided": 2}}
    _answer(st, sid, qs[0]["id"], "Ядро — сверка с плейбуком, потому что повторяется каждый день")
    r = Says(NEED)
    rv = senior.review_answer(st, sid, qs[0]["id"], router=r)
    assert rv["sample"] == "" and "образец не давай" in r.prompts[0]


def test_tired_junior_gets_short_mode():
    """Утомление считается внутри отрезка: два коротких ответа в «Границе» — короткий режим там, но не в «Ценности»."""
    st = state.create("Устал", 2)
    _answer(st, 2, "core_step", "не")
    _answer(st, 2, "write_action", "нет")
    sg = mentor.stage(st, 2, "access")
    assert sg["short_mode"] and sg["stage"] == 3
    assert not mentor.stage(st, 2, "loss_now")["short_mode"]


def test_step_budget_exhausted_goes_short():
    """Было: бюджет 4–6 на весь шаг. Стало: бюджет на отрезок, растёт с числом его вопросов."""
    st = state.create("Бюджет", 2)
    st["mentor"] = {"2:a": {"guided": 2}, "2:b": {"guided": 2}}  # прежние ключи вне ядра тоже считаются
    b = mentor.budget(2, "write_action")
    assert b > 4 and mentor.stage(st, 2, "write_action")["stage"] == 1
    st["mentor"] = {f"2:{q}": {"guided": 1} for q in core.segments(2)[0]["questions"][:b]}
    assert mentor.stage(st, 2, "write_action")["stage"] == 3
    assert mentor.stage(st, 2, "loss_now")["stage"] == 1  # у второго отрезка свой бюджет
    assert mentor.budget(3, "control_table") > mentor.budget(1, "process") >= mentor.BUDGET_MIN


def test_accepted_answer_offers_formulation_and_confirm_stores_it():
    st = state.create("Синтез", 1)
    _answer(st, 1, "process", "Договор приходит от контрагента, система сверяет с плейбуком")
    data = {"verdict": "accepted", "comment": "Принято.", "follow_up": "", "synthesis": "Система сверяет договор с плейбуком.",
            "risks": []}
    rv = senior.review_answer(st, 1, "process", router=Says(data))
    assert rv["synthesis"] == "Система сверяет договор с плейбуком."
    mentor.confirm_answer_synthesis(st, 1, "process", "Система сверяет договор с плейбуком и выдаёт отклонения.")
    assert state.get_answer(st, 1, "process")["synthesis"]["text"].endswith("отклонения.")
    with pytest.raises(ValueError):
        mentor.confirm_answer_synthesis(st, 1, "who_needs", "текст")


def test_step_synthesis_fills_template_and_shows_gaps(monkeypatch):
    s1 = dict(core.step(1))
    s1["synthesis"] = {"title": "Постановка проблемы", "template": "Мы хотим, чтобы [process]. Поддержать может [decision_maker: роль]."}
    monkeypatch.setattr(core, "step", lambda sid: s1 if int(sid) == 1 else core.steps()[int(sid) - 1])
    st = state.create("Вывод", 1)
    _answer(st, 1, "process", "система сверяла договоры.")
    d = mentor.step_synthesis_draft(st, 1)
    assert d["draft"] == "Мы хотим, чтобы система сверяла договоры. Поддержать может [роль: ещё нет ответа]."
    assert d["missing"] == ["decision_maker"]
    mentor.save_step_synthesis(st, 1, "Подтверждённый вывод")
    assert mentor.step_synthesis_draft(st, 1)["saved"]["text"] == "Подтверждённый вывод"


def test_prefill_from_previous_answers(monkeypatch):
    q = dict(core.question(2, "kpi_table"))
    q["prefill_from"] = ["loss_now"]
    real = core.question
    monkeypatch.setattr(core, "question", lambda sid, qid: q if (int(sid), qid) == (2, "kpi_table") else real(sid, qid))
    st = state.create("Черновик", 2)
    _answer(st, 2, "loss_now", "Срок согласования договора")
    rows = mentor.prefill(st, 2, "kpi_table")
    blank = [""] * (len(q["columns"]) - 1)
    assert rows == [["главное число ЛПР"] + blank, ["Срок согласования договора"] + blank]


def test_required_rows_prefilled_and_checked():
    """Обязательные строки ядра: подставляются в пустую таблицу и проверяются — черновик без содержания не засчитан."""
    from app import readiness
    st = state.create("Отказы", 3)
    q = core.question(3, "failures_table")
    rows = mentor.prefill(st, 3, "failures_table")
    assert [r[0] for r in rows[:2]] == q["required_rows"]
    state.set_answer(st, 3, "failures_table", "answered", rows + [["Вызов модели", "429", "код ответа", "повтор", "журнал", "4 попытки"]])
    miss = state.missing_required_rows(q, state.get_answer(st, 3, "failures_table")["value"])
    assert miss == q["required_rows"]
    assert any(r["qid"] == "failures_table" and r["reason"] == "Нет обязательной строки" for r in readiness.risks(st))
    filled = [["Приём файла", "плохой вход — мусор в файле", "формат", "отказ до модели", "человек", "отказ"],
              ["Сводка", "действие выполнено наполовину — сбой второго запроса", "ошибка", "карточка", "админ", "дозапись"],
              ["Вызов модели", "429", "код", "повтор", "журнал", "4 попытки"]]
    state.set_answer(st, 3, "failures_table", "answered", filled)
    assert state.missing_required_rows(q, filled) == []


def test_extra_text_kept_next_to_choice():
    st = state.create("Тайна", 2)
    state.set_answer(st, 2, "secret_kind", "answered", "Тайна в процессе — всё локально", extra="Сроки — квартал, бюджет ограничен")
    rec = state.get_answer(st, 2, "secret_kind")
    assert rec["extra"].startswith("Сроки")
    state.set_answer(st, 2, "secret_kind", "answered", "Тайны нет — всё по API")  # extra не передан — не теряется
    assert state.get_answer(st, 2, "secret_kind")["extra"].startswith("Сроки")
    r = Says({"verdict": "accepted", "comment": "Принято.", "follow_up": "", "synthesis": "", "risks": []})
    senior.review_answer(st, 2, "secret_kind", router=r)
    assert "Сроки — квартал" in r.prompts[0]


def test_pattern_card_prefills_pattern_answers():
    st = state.create("Паттерн", 3)
    assert mentor.prefill_text(st, 3, "pattern_answers") == ""
    state.set_answer(st, 3, "pattern", "answered", "Конвейер с валидатором")
    text = mentor.prefill_text(st, 3, "pattern_answers")
    assert text.startswith("Кто решает:") and "Где предел:" in text and "Что после предела:" in text


def test_no_numbers_only_in_its_segment():
    """Правило «без чисел» на Ш2 действует только в отрезке «Граница»."""
    assert core.no_numbers(1, "process")
    assert core.no_numbers(2, "core_step") and not core.no_numbers(2, "kpi_table")
    r = Says(NEED)
    st = state.create("Числа", 2)
    _answer(st, 2, "core_step", "Сверка задач")
    senior.review_answer(st, 2, "core_step", router=r)
    _answer(st, 2, "loss_now", "Час в день")
    senior.review_answer(st, 2, "loss_now", router=r)
    rule = "Отрезок «Граница» — без чисел"
    assert rule in r.prompts[0] and rule not in r.prompts[1]


def test_knowledge_advice_mixed_into_review_prompt_without_sources():
    """Совет базы знаний подмешивается в промпт проверки: по словам триггера, иначе первые по шагу; без основы."""
    import re
    st = state.create("Знания", 1)
    text = "Хочу внедрить нейросеть и агента"
    _answer(st, 1, "process", text)
    r = Says(NEED)
    senior.review_answer(st, 1, "process", router=r)
    p = r.prompts[0]
    assert "Опорные правила наставника" in p
    picked = senior.pick_knowledge(1, text)
    assert 2 <= len(picked) <= 3 and picked[0]["id"] == "SP1-01"
    assert all(a["advice"].strip()[:40] in p for a in picked)
    # основа совета в промпт не идёт; пометка источника — только разрешённая, в скобках
    assert not re.search(r"(?i)triz|agent-practice|basis|основа:", p)
    for a in picked:
        mark = senior.source_mark(a)
        assert (mark in p) if mark else True
    marks = re.findall(r"\(([^()]{3,60})\) Открыть можно вопросом", p)
    assert all(part.strip() in senior.SHOWN_SOURCES for m in marks for part in m.split(";")), marks
    assert senior.pick_knowledge(1, "zzz") == core.knowledge(1)[:2]  # совпадений нет — первые по шагу


def test_knowledge_source_mark_only_from_allowed_list():
    """Пометка источника у совета: только ТРИЗ / теория управления проектами / AI-native playbook; прочее — пусто."""
    assert senior.source_mark({"source": "ТРИЗ"}) == " (ТРИЗ)"
    assert senior.source_mark({"source": "AI-native playbook"}) == " (AI-native playbook)"
    assert senior.source_mark({"source": "OpenAI"}) == ""
    assert senior.source_mark({}) == ""
    assert senior.source_mark({"source": "ТРИЗ; AI-native playbook"}) == " (ТРИЗ; AI-native playbook)"
    assert senior.source_mark({"source": "ТРИЗ; OpenAI"}) == " (ТРИЗ)"
    parts = lambda a: [p.strip() for p in str(a.get("source") or "").split(";") if p.strip()]
    triz = [a for a in core.knowledge() if a["basis"] in ("triz", "both")]
    assert triz and all("ТРИЗ" in parts(a) for a in triz)
    # советы практик агентных систем без пометки, кроме взятых из AI-native playbook
    assert all(parts(a) in (["исследование рынка аналогичных проектов и практика"], ["AI-native playbook"]) for a in core.knowledge() if a["basis"] == "agent-practice")
    assert len([a for a in core.knowledge() if "AI-native playbook" in parts(a)]) == 15


def test_all_advice_have_shown_source_and_question_open():
    """Решение владелицы 04.11.2026: у каждого совета есть источник; вопрос о нём открыт, устройство copilot закрыто."""
    from app import guard
    assert all(senior.source_mark(a) for a in core.knowledge())
    assert senior.source_mark({"source": "исследование рынка аналогичных проектов и практика"}) == " (исследование рынка аналогичных проектов и практика)"
    assert guard.is_advice_source_question("Какой источник у этой подсказки?")
    assert guard.is_advice_source_question("Откуда этот совет?")
    assert not guard.is_advice_source_question("Откуда взята структура маршрута?")
    assert not guard.is_advice_source_question("Какой источник у советов Senior Pilot?")


def test_source_mark_passes_output_guard_but_origin_question_stays_closed():
    """Совет с пометкой «(ТРИЗ)» проходит выходной фильтр без изменений; рассказ о происхождении copilot — закрыт."""
    from app import guard
    advice = "Не ищите середину между требованиями — разведите их по условию или во времени (ТРИЗ)."
    assert guard.filter_output(advice) == (advice, False)
    assert guard.filter_output("Маршрут copilot построен на ТРИЗ и практиках вендоров.")[1] is True


def test_saved_project_moves_answers_to_new_places():
    """Схема 1 → 3 одним переездом: прежние переносы (схема 2) и новый маршрут из 6 шагов."""
    st = state.create("Переезд", 1)
    st["schema"] = 1
    st["answers"] = {"1": {"origin": {"status": "answered", "value": "Письмо от контрагента", "note": "", "updated": "x"}},
                     "2": {"traces": {"status": "answered", "value": "Почта юристов", "note": "", "updated": "x"},
                           "data_where": {"status": "answered", "value": "Папка договоров", "note": "", "updated": "x"}}}
    st["risk_owners"] = {"q:1:origin": "юрист"}
    state.save(st, "старая схема")
    back = state.load(st["id"])
    assert back["answers"]["1"]["as_is_table"]["value"][0][0].startswith("Письмо от контрагента")
    assert back["answers"]["1"]["data_where"]["value"] == "Папка договоров\nПочта юристов"
    assert back["risk_owners"] == {"q:1:as_is_table": "юрист"}
    assert back["schema"] == mentor.SCHEMA == 3
