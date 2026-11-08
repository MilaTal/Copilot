"""Нет тупиков: в любом состоянии проекта у Junior Pilot есть рекомендация, которая либо двигает вперёд (advance),
либо говорит, что доработать (rework). Документ шага собирается тогда и только тогда, когда шаг закрыт, а иначе
сообщение называет каждую причину. Без сети, на временной папке данных (conftest), без вызова модели.

Проверяется вперёд (Ш1→Ш6), назад (проект начат с Ш6, шаги Ш6→Ш1), на сценарии «сверка счетов» с вердиктами
Senior Pilot по разметке и кругом доработки, и при всех отложенных или отклонённых рекомендациях."""
import copy
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app import artifacts, core, defense, readiness, recommend, state
from app.main import app
from scenarios import run_scenario as rs

SC = yaml.safe_load((Path(__file__).resolve().parent.parent / "scenarios" / "demo_scenario.yaml")
                    .read_text(encoding="utf-8"))
BY = {(int(a["step"]), a["qid"]): a for a in SC["answers"]}
IDS = core.step_ids()
LAST = IDS[-1]
FOLLOW = "добавьте срок, владельца и число"
CHECKED: list[str] = []  # подписи проверенных состояний — для счёта в отчёте


# ---------- ответы ----------
def good(step_id: int, qid: str):
    a = BY[(step_id, qid)]
    return a.get("rework") or a["value"]


def put(st: dict, step_id: int, qid: str, value=None, status="answered", note=""):
    a = BY[(step_id, qid)]
    state.set_answer(st, step_id, qid, status, (good(step_id, qid) if value is None else value)
                     if status == "answered" else None, note, a.get("extra"))


def required(step_id: int, qid: str) -> bool:
    return core.question(step_id, qid).get("required", True)


def weak_qid(step_id: int) -> str:
    """Намеренно слабый ответ шага на обязательный вопрос (только он держит сборку документа)."""
    return next(a["qid"] for a in SC["answers"] if int(a["step"]) == step_id and a["expect"] == "needs_detail"
                and required(step_id, a["qid"]))


def risky_q(step_id: int) -> dict | None:
    return next((q for q in core.step(step_id)["questions"] if q.get("risky_options")), None)


def safe_value(step_id: int, q: dict):
    """Ответ из сценария, но без рискованного варианта — чтобы шаг был закрыт без риска выбора."""
    v = good(step_id, q["id"])
    risky = set(q.get("risky_options") or [])
    if q["type"] == "choice" and v in risky:
        return next(o for o in q["options"] if o not in risky)
    if q["type"] == "multi":
        return [x for x in v if x not in risky] or [next(o for o in q["options"] if o not in risky)]
    return v


def answer_all(st: dict, step_id: int) -> None:
    for q in core.step(step_id)["questions"]:
        put(st, step_id, q["id"], safe_value(step_id, q))


# ---------- проверки ----------
WHERE = ("Откройте", "На шаге", "В итоге шага", "Отложено до шага", "В плане после запуска")


def assert_path(i: dict, label: str) -> None:
    """Путь: первая строка — где в copilot (шаг, экран, кнопка), в середине — что сделать, последняя — как понять,
    что готово."""
    p = i.get("path") or []
    assert len(p) >= 2, f"{label}: у «{i['title']}» нет пути по шагам"
    assert p[0].startswith(WHERE), f"{label}: путь «{i['title']}» не говорит, куда идти: {p[0]}"
    assert p[-1].startswith("Готово"), f"{label}: путь «{i['title']}» не говорит, как понять, что готово"
    assert all(isinstance(x, str) and x.strip() for x in p), label


def statuses(st):
    return {x["step"]: x for x in readiness.all_status(st)}


def check(st: dict, label: str, step_id: int, back: bool = False) -> dict:
    """Общие проверки состояния. Возвращает view, чтобы тест мог досмотреть частное."""
    CHECKED.append(label)
    v = recommend.view(st)
    nxt = v["next"]
    ss = statuses(st)
    cur = int(st["current_step"])
    assert nxt, f"{label}: тупик — «Следующий шаг» пуст"
    for i in nxt:
        assert i["kind"] in ("advance", "rework"), f"{label}: {i['title']} без kind"
        assert i["title"] and isinstance(i["why"], str) and isinstance(i["how"], list), label
    cand = recommend.candidates(st)
    assert all(r.get("kind") in ("advance", "rework") for r in cand.values()), label
    # у каждой рекомендации — путь по шагам в copilot
    for i in nxt + [x for xs in v["plan"].values() for x in xs] + v["after_launch"]:
        assert_path(i, label)

    prim = [i for i in nxt if i.get("nav") == "primary"]
    assert len(prim) == 1, f"{label}: нет навигации в «Следующем шаге»"
    p = prim[0]
    if ss[cur]["can_build"]:
        assert p["kind"] == "advance", f"{label}: шаг закрыт, а хода вперёд нет — {p['title']}"
        later = [s for s in IDS if s > cur and not ss[s]["can_build"]]
        earlier = [s for s in IDS if s < cur and not ss[s]["can_build"]]
        if later or earlier:
            t = later[0] if later else earlier[-1]
            if back and not later:
                assert t == cur - 1 or ss[cur - 1]["can_build"], f"{label}: назад — ближайший шаг {core.code(cur - 1)}"
            assert p["title"].startswith(f"Перейдите к шагу {core.code(t)} "), f"{label}: {p['title']}"
            if not back and cur < LAST:
                assert t == cur + 1, f"{label}: вперёд — следующий шаг {core.code(cur + 1)}, а не {core.code(t)}"
        else:
            assert p["title"].startswith(("Соберите каркас защиты", "Проект готов к защите")), f"{label}: {p['title']}"
    else:
        bl = readiness.build_blockers(st, cur)
        assert bl and p["kind"] == "rework" and p["step"] == cur, f"{label}: {p['title']}"
        assert p["title"].startswith(f"Ответьте на открытые вопросы шага {core.code(cur)} (открыто {len(bl)} из "), label
        first = recommend._short(bl[0]["text"])
        assert any(first in h for h in p["how"]), f"{label}: рекомендация не называет открытый вопрос"

    # прошлые незакрытые шаги — «Вернитесь к шагу», пока не закрыты; закрытые — без напоминаний
    for k in IDS:
        rid = recommend._rid("return", k)
        if k < cur and not ss[k]["can_build"]:
            assert rid in cand and cand[rid]["kind"] == "rework", f"{label}: нет «Вернитесь к шагу {core.code(k)}»"
            assert cand[rid]["title"].startswith(f"Вернитесь к шагу {core.code(k)} "), label
            assert any(i["id"] == rid for i in v["plan"][core.code(k)]), label
        else:
            assert rid not in cand, f"{label}: лишнее «Вернитесь к шагу {core.code(k)}»"
    rets = sorted((r for r in cand.values() if r.get("nav") == "return"), key=lambda r: r["step"])
    if rets:
        assert rets[0]["id"] in [i["id"] for i in nxt], f"{label}: ранний незакрытый шаг не в «Следующем шаге»"

    # документ шага: собирается тогда и только тогда, когда шаг закрыт; иначе — каждая причина словами
    probe = copy.deepcopy(st)
    if ss[step_id]["can_build"]:
        artifacts.build_step(probe, step_id)
    else:
        with pytest.raises(ValueError) as e:
            artifacts.build_step(probe, step_id)
        msg = str(e.value)
        bl = readiness.build_blockers(st, step_id)
        assert bl and msg
        for b in bl:
            head = {"needs_detail": "нужно уточнить: «", "missing": "нет ответа: «"}[b["reason"]]
            assert head + readiness._short(b["text"], 140)[:60] in msg, f"{label}: в сообщении нет {b['qid']}"
            if b["follow_up"]:
                assert b["follow_up"][:40] in msg, f"{label}: нет подсказки Senior Pilot для {b['qid']}"
        assert "«Не знаю» или «Опускаю»" in msg
    return v


def walk_step(st: dict, n: int, back: bool, tag: str) -> None:
    """Все состояния одного шага по порядку."""
    st["current_step"] = n
    check(st, f"{tag} {core.code(n)}: шаг пуст", n, back)

    qs = core.step(n)["questions"]
    for q in qs[: len(qs) // 2]:
        put(st, n, q["id"], safe_value(n, q))
    check(st, f"{tag} {core.code(n)}: часть вопросов отвечена", n, back)

    answer_all(st, n)
    wq = weak_qid(n)
    state.set_senior_review(st, n, wq, {"verdict": "needs_detail", "comment": "общо", "follow_up": FOLLOW, "risks": []})
    v = check(st, f"{tag} {core.code(n)}: один ответ «нужно уточнить»", n, back)
    p = next(i for i in v["next"] if i.get("nav") == "primary")
    assert p["kind"] == "rework" and any(FOLLOW in h for h in p["how"])
    assert not readiness.step_status(st, core.step(n))["can_build"]

    put(st, n, wq, status="unknown", note="данных пока нет, выясняет владелец процесса")
    check(st, f"{tag} {core.code(n)}: один «Не знаю»", n, back)
    put(st, n, wq)

    rq = risky_q(n)
    if rq:
        put(st, n, rq["id"], rq["risky_options"][0] if rq["type"] == "choice" else [rq["risky_options"][0]])
        check(st, f"{tag} {core.code(n)}: рискованный вариант", n, back)
        assert any(r["kind"] == "rework" and r.get("qid") == rq["id"] for r in recommend.candidates(st).values())
        put(st, n, rq["id"], safe_value(n, rq))

    check(st, f"{tag} {core.code(n)}: всё закрыто, документа нет", n, back)
    assert recommend._rid("doc", n) in recommend.candidates(st)
    assert recommend.candidates(st)[recommend._rid("doc", n)]["kind"] == "advance"

    artifacts.build_step(st, n)
    check(st, f"{tag} {core.code(n)}: документ собран", n, back)
    assert recommend._rid("doc", n) not in recommend.candidates(st)


def expected_states() -> int:
    """6 состояний на шаг, ещё одно — где в шаге есть рискованный вариант, переходы между шагами и два финальных."""
    return 6 * len(IDS) + sum(1 for n in IDS if risky_q(n)) + (len(IDS) - 1) + 2


def finish_checks(st: dict, tag: str) -> None:
    ss = statuses(st)
    assert all(x["can_build"] for x in ss.values())
    v = check(st, f"{tag}: все шаги закрыты, каркаса защиты нет", int(st["current_step"]))
    assert any(i["kind"] == "advance" and i["title"].startswith("Соберите каркас защиты") for i in v["next"])
    defense.for_project(st)
    v = check(st, f"{tag}: каркас защиты собран", int(st["current_step"]))
    fin = [i for i in v["next"] if i.get("nav") == "primary"]
    assert fin and fin[0]["kind"] == "advance" and fin[0]["title"].startswith("Проект готов к защите: все 6 шагов закрыты")


# ---------- 1. вперёд ----------
def test_forward_no_dead_ends():
    CHECKED.clear()
    st = state.create("Вперёд", 1, domain=SC["project"]["domain"])
    for n in IDS:
        walk_step(st, n, False, "вперёд")
        if n < LAST:
            st["current_step"] = n + 1
            v = check(st, f"вперёд {core.code(n)}→{core.code(n + 1)}: переход", n + 1)
            assert recommend._rid("advance", n + 1) not in recommend.candidates(st)  # переход сделан — ход закрылся
            assert v["next"][0]["nav"] == "primary"
    finish_checks(st, "вперёд")
    assert len(CHECKED) == expected_states(), len(CHECKED)


# ---------- 2. назад ----------
def test_backward_from_step6_no_dead_ends():
    CHECKED.clear()
    st = state.create("Назад", LAST, domain=SC["project"]["domain"])
    assert st["current_step"] == LAST and st["start_step"] == LAST
    for n in reversed(IDS):
        walk_step(st, n, True, "назад")
        if n > 1:
            st["current_step"] = n - 1
            check(st, f"назад {core.code(n)}→{core.code(n - 1)}: переход", n - 1, True)
    finish_checks(st, "назад")
    assert len(CHECKED) == expected_states(), len(CHECKED)


def test_going_back_does_not_close_prototype_and_defense_with_praise():
    """Найдено 12.10.2026: рекомендации прототипа и защиты считались от текущего шага — ответ на прошлом шаге
    снимал их, и они закрывались сами с похвалой. Теперь — от самого дальнего шага, до которого проект доходил."""
    st = state.create("Назад и прототип", LAST)
    recommend.view(st)
    for rid in (recommend._rid("proto"), recommend._rid("defense")):
        assert rid in recommend.candidates(st)
    put(st, 1, "process")  # current_step стал 1
    recommend.view(st)
    for rid in (recommend._rid("proto"), recommend._rid("defense")):
        assert rid in recommend.candidates(st) and st["recs"][rid]["status"] == "new"


# ---------- 3. сценарий «сверка счетов» в обе стороны ----------
@pytest.mark.parametrize("order", ["вперёд", "назад"])
def test_demo_scenario_with_rework_both_ways(order):
    CHECKED.clear()
    p = SC["project"]
    start = 1 if order == "вперёд" else LAST
    st = state.create(p["name"], start, p.get("description", ""), p.get("domain", ""))
    steps = IDS if order == "вперёд" else list(reversed(IDS))
    rows = []
    for n in steps:
        items = [a for a in SC["answers"] if int(a["step"]) == n]
        for a in items:
            state.set_answer(st, n, a["qid"], "answered", a["value"], a.get("note", ""), a.get("extra"))
        for a in items:  # вердикт Senior Pilot по разметке сценария
            if rs.norm_expect(a.get("expect")) == "needs_detail":
                rs.apply_review(st, n, a["qid"], rs.markup_review(a))
        weak = [a for a in items if a["expect"] == "needs_detail"]
        assert weak
        check(st, f"сценарий {order} {core.code(n)}: слабые ответы на доработке", n, order == "назад")
        assert not readiness.step_status(st, core.step(n))["can_build"]
        msg = readiness.build_block_message(st, n)
        for a in weak:  # необязательный вопрос «нужно уточнить» сборку не держит — он в рекомендациях как rework
            text = readiness._short(core.question(n, a["qid"])["text"], 140)[:60]
            if required(n, a["qid"]):
                assert "нужно уточнить: «" + text in msg
            else:
                assert text not in msg
                assert any(r["kind"] == "rework" and r.get("qid") == a["qid"] for r in recommend.candidates(st).values())
        rows += rs.rework_step(st, n, items, None, True)  # круг доработки без модели
        check(st, f"сценарий {order} {core.code(n)}: после доработки", n, order == "назад")
        artifacts.build_step(st, n)
        check(st, f"сценарий {order} {core.code(n)}: документ собран", n, order == "назад")
    assert len(rows) == 12 and all(r["after"] == "accepted" for r in rows)
    assert len(st["artifacts"]) == 6 and not any(a["stale"] for a in st["artifacts"].values())
    assert all(x["can_build"] for x in readiness.all_status(st))
    v = recommend.view(st)
    assert v["next"] and any(i["kind"] == "advance" for i in v["next"])


# ---------- 4. всё отложено или отклонено ----------
def _mute_all(st: dict, action: str) -> dict:
    """Откладывает или отклоняет всё, что видно в «Следующем шаге», пока там не останется только страховка.
    С 12.10.2026 ход по маршруту и «важно до запуска» на последнем шаге не откладываются — их отклоняют с причиной;
    на последнем шаге отложенное уходит в план после запуска с владельцем и стадией."""
    last = int(st["current_step"]) == LAST
    for _ in range(80):
        v = recommend.view(st)
        live = [i for i in v["next"] if not i.get("fallback")]
        if not live:
            return v
        for i in live:
            if action == "defer" and not i.get("nav") and not (last and i["level"] == "warning"):
                recommend.act(st, i["id"], "defer", reason="тень" if last else "", owner="инженер сопровождения")
            else:
                recommend.act(st, i["id"], "dismiss", reason="проверка страховки")
    raise AssertionError("рекомендации не кончаются")


@pytest.mark.parametrize("action", ["dismiss", "defer"])
@pytest.mark.parametrize("where", ["пустой Ш1", "Ш2 закрыт", "назад с Ш6", "всё закрыто"])
def test_everything_muted_still_has_navigation(action, where):
    if where == "пустой Ш1":
        st = state.create("Страховка", 1, domain=SC["project"]["domain"])
    elif where == "Ш2 закрыт":
        st = state.create("Страховка", 1)
        answer_all(st, 1)
        answer_all(st, 2)
    elif where == "назад с Ш6":
        st = state.create("Страховка", LAST)
        answer_all(st, LAST)
    else:
        st = state.create("Страховка", 1)
        for n in IDS:
            answer_all(st, n)
    CHECKED.append(f"страховка: {where}, {action}")
    v = _mute_all(st, action)
    assert v["next"] and all(i["kind"] in ("advance", "rework") for i in v["next"]), where
    for i in v["next"] + v["after_launch"]:
        assert_path(i, where)
    if action == "defer" and int(st["current_step"]) == LAST:
        # до 12.10.2026 отложенное на последнем шаге возвращалось сразу; теперь оно в плане после запуска
        assert v["after_launch"] and all(i["stage"] == "тень" and i["owner"] for i in v["after_launch"]), where
    assert len(v["next"]) == 1 and v["next"][0]["fallback"] and v["next"][0]["nav"] == "primary", where
    # и после повторного отклонения навигация возвращается
    recommend.act(st, v["next"][0]["id"], "dismiss", reason="ещё раз")
    again = recommend.view(st)["next"]
    assert again and again[0]["id"] == v["next"][0]["id"]


def test_api_returns_kind_and_fallback():
    st = state.create("API страховка", 1)
    state.save(st, "тест")
    c = TestClient(app)
    v = c.get(f"/api/projects/{st['id']}/recs").json()
    assert v["next"] and all(i["kind"] in ("advance", "rework") for i in v["next"])
    for _ in range(40):
        live = [i for i in v["next"] if not i.get("fallback")]
        if not live:
            break
        for i in live:
            v = c.post(f"/api/projects/{st['id']}/recs/{i['id']}", json={"action": "dismiss", "reason": "тест"}).json()["recs"]
    assert v["next"] and v["next"][0]["fallback"]
