"""Строгость внутри шага, свобода между шагами, риски из «Не знаю» и «Опускаю», версии."""
import pytest

from app import artifacts, core, readiness, state


def fill_step(st, step_id, status="answered"):
    for q in core.step(step_id)["questions"]:
        if not q.get("required", True):
            continue
        if status != "answered":
            state.set_answer(st, step_id, q["id"], status)
        elif q["type"] == "table":
            req = [[lbl] + [f"{c} 1" for c in q["columns"][1:]] for lbl in q.get("required_rows") or []]
            rest = max(0, q.get("min_rows", 1) - len(req))
            state.set_answer(st, step_id, q["id"], "answered", req + [[f"{c} 1" for c in q["columns"]]] * rest)
        elif q["type"] in ("choice",):
            state.set_answer(st, step_id, q["id"], "answered", q["options"][0])
        elif q["type"] == "multi":
            state.set_answer(st, step_id, q["id"], "answered", q["options"][:2])
        else:
            state.set_answer(st, step_id, q["id"], "answered", "ответ с числом 42")


def test_empty_answer_refused_buttons_offered():
    st = state.create("Проект", 1)
    with pytest.raises(ValueError, match="Не знаю"):
        state.set_answer(st, 1, "process", "answered", "  ")


def test_choice_must_be_from_options():
    st = state.create("Проект", 1)
    with pytest.raises(ValueError):
        state.set_answer(st, 2, "access", "answered", "что-то своё")  # access живёт на Ш2


def test_build_blocked_until_every_required_question_closed():
    st = state.create("Проект", 1)
    state.set_answer(st, 1, "process", "answered", "Собрать протокол")
    # было: «Без ответа: …» — только вопросы без ответа; стало (12.10.2026): каждая причина словами и что сделать
    with pytest.raises(ValueError, match="нет ответа: «") as e:
        artifacts.build_step(st, 1)
    assert "не закрыто обязательных вопросов — 7 из 8" in str(e.value) and "«Не знаю» или «Опускаю»" in str(e.value)
    fill_step(st, 1)
    res = artifacts.build_step(st, 1)
    assert res["path"].startswith("Ш1")  # было: документ шага с прежним кодом


def test_unknown_and_skipped_close_question_but_become_risks():
    st = state.create("Проект", 1)
    fill_step(st, 1, "unknown")
    ss = readiness.step_status(st, core.step(1))
    assert ss["can_build"] and ss["full"] == 0 and ss["label"] == "closed_with_risks"
    rs = [r for r in readiness.risks(st) if r["step"] == 1]
    assert len(rs) == ss["required"] and all(r["reason"] == "Ответ «Не знаю»" for r in rs)
    md = artifacts.build_step(st, 1)["markdown"]
    assert "Рекомендации для развития" in md and "Не знаю" in md


def test_moving_to_any_step_is_never_blocked():
    st = state.create("Проект", 3)
    state.set_answer(st, 5, "attack_target", "answered", "стенд с копией базы")
    assert st["current_step"] == 5
    lamps = {l["step"]: l["light"] for l in readiness.annunciator(st)}
    assert len(lamps) == core.step_count() == 6
    assert lamps[1] == "idle" and lamps[5] in ("caution", "warning")
    with pytest.raises(ValueError, match="от 1 до 6"):
        state.create("Проект", 7)  # было: старт с любой из 12 точек


def test_needs_detail_from_senior_reopens_question():
    st = state.create("Проект", 1)
    state.set_answer(st, 2, "loss_now", "answered", "много времени")
    state.set_senior_review(st, 2, "loss_now", {"verdict": "needs_detail", "follow_up": "Сколько минут в неделю?"})
    ss = readiness.step_status(st, core.step(2))
    assert any(o["qid"] == "loss_now" and o["reason"] == "needs_detail" for o in ss["open"])
    assert not ss["can_build"]


def test_table_with_too_few_rows_is_a_risk():
    st = state.create("Проект", 2)
    state.set_answer(st, 1, "as_is_table", "answered", [["шаг", "роль", "Word", "неудобно"]])
    assert any(r["qid"] == "as_is_table" and r["reason"] == "Мало строк" for r in readiness.risks(st))


def test_versions_and_restore():
    st = state.create("Проект", 1)
    state.set_answer(st, 1, "process", "answered", "первая версия")
    state.save(st, "первая")
    state.set_answer(st, 1, "process", "answered", "вторая версия")
    state.save(st, "вторая")
    vs = state.versions(st["id"])
    assert len(vs) >= 3
    old = next(v for v in vs if v["what"] == "первая")
    back = state.restore(st["id"], old["version"])
    assert back["answers"]["1"]["process"]["value"] == "первая версия"


def test_artifact_marked_stale_after_answer_changes():
    st = state.create("Проект", 1)
    fill_step(st, 1)
    artifacts.build_step(st, 1)
    state.set_answer(st, 1, "process", "answered", "новая формулировка")
    assert readiness.step_status(st, core.step(1))["artifact_stale"]


def test_debrief_message_does_not_block():
    st = state.create("Проект", 1)
    state.set_answer(st, 1, "process", "answered", "Собрать протокол")
    d = readiness.step_debrief(st, 1)
    assert "Дальше идти можно" in d["message"]


def test_project_id_cannot_escape_data_dir():
    with pytest.raises(ValueError):
        state.project_dir("../../etc")
