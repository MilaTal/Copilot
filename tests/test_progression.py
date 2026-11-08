"""От простого к сложному: Ш1 — проблема словами, числа появляются позже; уровни шагов; мягкий тон табло."""
import re

from app import core, readiness, senior, state


def test_step1_is_problem_in_words():
    """Было: первый шаг — только идея из 6 вопросов. Стало: Ш1 «Проблема» — отрезки «Идея» и «Как сейчас», без чисел."""
    s = core.step(1)
    assert s["title"] == "Проблема" and s["level"] == "sketch"
    assert s["segments"][0]["questions"] == ["process", "why_company", "who_needs", "if_nothing",
                                             "success_picture", "decision_maker"]
    assert s["segments"][1]["questions"] == ["as_is_table", "bottleneck", "incidents", "reality_gap", "data_where"]
    for q in s["questions"]:
        assert q["type"] in ("text", "table")
        assert not re.search(r"(?i)числом|сколько|в неделю|в месяц|минут|процент", q["text"]), q["text"]


def test_numbers_moved_where_they_are_born():
    """Было: потери и KPI на WP04, ограничения на WP03. Стало: числа рождаются во втором отрезке Ш2, тайна — на Ш2."""
    where = {q["id"]: s["id"] for s in core.steps() for q in s["questions"]}
    assert where["loss_now"] == 2 and where["kpi_table"] == 2 and where["secret_kind"] == 2 and where["access"] == 2
    assert where["data_where"] == 1 and where["key_rotation"] == 4 and where["break_even"] == 5 and where["pilot_scope"] == 6
    for gone in ("traces", "proof", "origin", "business_kpi", "constraints", "human_refusal", "fate_table",
                 "confirm_what_runs", "bad_input", "partial_failure", "manual_queue", "cost_limit_place", "excessive_agency"):
        assert gone not in where, gone  # слиты в поглощающие вопросы
    assert core.segment_index(2, "loss_now") == 2 and core.step(2)["no_numbers_segment"] == 1


def test_levels_grow():
    order = ["sketch", "project", "design", "calc"]
    seq = [order.index(s["level"]) for s in core.steps()]
    assert seq == sorted(seq) and seq[0] == 0 and seq[-1] == 3
    assert set(core.levels()) == set(order)


def test_senior_prompt_carries_level(monkeypatch):
    seen = {}

    class R:
        def chat(self, role, system, messages, **kw):
            seen["text"] = messages[0]["content"]
            raise senior.RouterError("стоп")
    st = state.create("Уровень", 1)
    state.set_answer(st, 1, "process", "answered", "Проверять договоры")
    try:
        senior.review_answer(st, 1, "process", router=R())
    except senior.RouterError:
        pass
    assert "Уровень шага: Набросок" in seen["text"] and "Ни чисел, ни расчётов не требуй" in seen["text"]


def test_senior_advice_does_not_light_the_board():
    st = state.create("Табло", 1)
    for q in core.step(1)["questions"]:
        if q["type"] == "table":
            state.set_answer(st, 1, q["id"], "answered", [[f"шаг {i}", "роль", "где", "неудобно"] for i in range(3)])
        else:
            state.set_answer(st, 1, q["id"], "answered", "ответ словами")
    state.add_risk(st, 1, "Совет Senior Pilot", "caution", "senior")
    lamp = next(l for l in readiness.annunciator(st) if l["step"] == 1)
    assert lamp["light"] == "off"
    ov = readiness.overall(st)
    assert ov["advice"] == 1 and ov["cautions"] == 0


def test_debrief_starts_with_what_is_done():
    st = state.create("Похвала", 1)
    state.set_answer(st, 1, "process", "answered", "Идея")
    msg = readiness.step_debrief(st, 1)["message"]
    req = sum(1 for q in core.step(1)["questions"] if q.get("required", True))
    assert req == 8 and msg.startswith(f"Сделано: 1 из {req}") and "Дальше идти можно" in msg
