"""Демонстрационный сценарий (вымышленные данные) написан на 6 шагов: ответ на каждый вопрос ядра в форме вопроса,
разметка намеренно слабых ответов, без чисел там, где ядро их запрещает. Падение здесь значит, что ядро ушло вперёд,
а сценарий отстал: на нём стоят проверки прогона и отсутствия тупиков в пакете. Без сети."""
import re
from pathlib import Path

import pytest
import yaml

from app import consistency, core, readiness, state

SC_PATH = Path(__file__).resolve().parent.parent / "scenarios" / "demo_scenario.yaml"


@pytest.fixture(scope="module")
def sc():
    return yaml.safe_load(SC_PATH.read_text(encoding="utf-8"))


def _by_key(sc):
    return {(int(a["step"]), a["qid"]): a for a in sc["answers"]}


def test_native_schema_and_project(sc):
    assert sc["schema"] == 3
    assert sc["project"]["name"] == "Вымышленный пример: сверка счетов с заказами"
    assert core.domain(sc["project"]["domain"])


def test_every_core_question_has_exactly_one_answer(sc):
    expected = {(s["id"], q["id"]) for s in core.steps() for q in s["questions"]}
    keys = [(int(a["step"]), a["qid"]) for a in sc["answers"]]
    assert len(expected) == 68
    assert len(keys) == len(set(keys)), "вопрос отвечен дважды"
    assert set(keys) == expected


def test_answer_type_matches_question_type(sc):
    for (step_id, qid), a in _by_key(sc).items():
        q = core.question(step_id, qid)
        v = a["value"]
        where = f"{core.code(step_id)}.{qid}"
        assert a.get("status", "answered") == "answered", where
        if q["type"] == "table":
            assert isinstance(v, list) and len(v) >= int(q.get("min_rows") or 1), where
            for row in v:
                assert isinstance(row, list) and len(row) == len(q["columns"]), f"{where}: {row}"
                assert all(str(c).strip() for c in row), f"{where}: пустая ячейка в {row}"
            assert not state.missing_required_rows(q, v), where
        elif q["type"] == "choice":
            assert isinstance(v, str) and v in q["options"], where
        elif q["type"] == "multi":
            assert isinstance(v, list) and v and all(x in q["options"] for x in v), where
        else:
            assert isinstance(v, str) and v.strip(), where
        if q.get("extra_text"):
            assert str(a.get("extra") or "").strip(), f"{where}: нет строки «{q['extra_text']}»"
        else:
            assert "extra" not in a, where


def test_no_numbers_where_core_forbids_them(sc):
    for (step_id, qid), a in _by_key(sc).items():
        if core.no_numbers(step_id, qid):
            text = yaml.safe_dump([a["value"], a.get("extra", ""), a.get("note", "")], allow_unicode=True)
            assert not re.search(r"\d", text), f"{core.code(step_id)}.{qid}: число в отрезке без чисел"


def test_weak_answers_marked_two_per_step(sc):
    for s in core.steps():
        mine = [a for a in sc["answers"] if int(a["step"]) == s["id"]]
        assert all(a["expect"] in ("accepted", "needs_detail") for a in mine)
        weak = [a for a in mine if a["expect"] == "needs_detail"]
        assert len(weak) == 2, s["code"]
        assert all(a.get("flaw", {}).get("what") and a["flaw"].get("signal") for a in weak), s["code"]


def test_every_weak_answer_has_rework_and_only_weak(sc):
    weak = [a for a in sc["answers"] if a["expect"] == "needs_detail"]
    assert len(weak) == 12
    for a in sc["answers"]:
        where = f"{core.code(int(a['step']))}.{a['qid']}"
        if a["expect"] != "needs_detail":
            assert "rework" not in a, where
            continue
        rw = a.get("rework")
        assert isinstance(rw, str) and len(rw) > 2 * len(str(a["value"])), where
        if core.no_numbers(int(a["step"]), a["qid"]):
            assert not re.search(r"\d", rw), f"{where}: число в доработке на отрезке без чисел"


def test_reworked_scenario_has_no_contradictions(sc):
    st = {"id": "x", "answers": {}, "artifacts": {}}
    for a in sc["answers"]:
        state.set_answer(st, int(a["step"]), a["qid"], "answered", a.get("rework") or a["value"], a.get("note", ""),
                         a.get("extra"))
    assert consistency.check(st) == []


def test_attack_questions_filled_and_findings_not_run(sc):
    by = _by_key(sc)
    s5 = core.qstep("attack_selection")
    for qid in ("untrusted_inputs", "attack_target", "attack_selection", "attack_expectations", "attack_findings"):
        assert by[(s5, qid)]["value"], qid
    assert "ещё не запускались" in by[(s5, "attack_findings")]["value"].lower()
    assert "стенд" in by[(s5, "attack_target")]["value"].lower()


def test_scenario_loads_into_state_without_contradictions(sc):
    st = {"id": "x", "answers": {}, "artifacts": {}}
    for a in sc["answers"]:
        state.set_answer(st, int(a["step"]), a["qid"], "answered", a["value"], a.get("note", ""), a.get("extra"))
    assert consistency.check(st) == []
    for ss in readiness.all_status(st):
        assert ss["can_build"] and not ss["open"], ss["code"]
    assert state.get_answer(st, core.qstep("secret_kind"), "secret_kind").get("extra")
