"""Рекомендательная система Senior Pilot: отбор, ранжирование, действия, перепроверка «сделано», кивок вместо похвалы."""
import pytest
from fastapi.testclient import TestClient

from app import core, recommend, state
from app.main import app

c = TestClient(app)


class FakeRouter:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def chat(self, role, system, messages, **kw):
        self.calls += 1
        return type("R", (), {"text": self.text, "cost_usd": 0.01, "model": "fake"})()


def _fresh(name, step=1, **kw):
    st = state.create(name, step, **kw)
    state.set_answer(st, 1, "why_company", "answered", "Юристы тонут в рутине")  # шаг начат — пропуски видны
    return st


def _by_qid(st, qid):
    return next(r for r in recommend.candidates(st).values() if r.get("qid") == qid)


def test_open_question_becomes_recommendation_with_hint():
    st = _fresh("Рек")
    r = _by_qid(st, "process")
    assert r["source"] == "question" and r["step"] == 1 and r["how"] and r["title"].startswith("Ответьте на вопрос")


def test_score_prefers_current_step_and_low_effort():
    base = {"impact": 2, "effort": 1}
    assert recommend._score({**base, "step": 1}, 1) > recommend._score({**base, "step": 2}, 1) > \
        recommend._score({**base, "step": 5}, 1)
    assert recommend._score({**base, "step": 1}, 1) > recommend._score({"impact": 2, "effort": 3, "step": 1}, 1)


def test_view_gives_top3_and_full_plan():
    st = _fresh("План")
    v = recommend.view(st)
    assert len(v["next"]) == 3 and all(i["status"] == "new" for i in v["next"])
    req = sum(1 for q in core.step(1)["questions"] if q.get("required", True))
    # было: 5 обязательных вопросов первого шага; стало: 8 на Ш1 (отрезки «Идея» и «Как сейчас»), один отвечен.
    # С 12.10.2026 плюс одна навигационная «Ответьте на открытые вопросы шага Ш1» — она первая в «Следующем шаге»
    assert "Ш1" in v["plan"] and sum(len(x) for x in v["plan"].values()) == req - 1 + 1 == 8
    assert v["next"][0]["nav"] == "primary" and v["next"][0]["kind"] == "rework"
    scores = [i["score"] for i in v["next"]]
    assert scores == sorted(scores, reverse=True)


def test_done_is_rechecked_when_condition_still_there():
    st = _fresh("Перепроверка")
    recommend.view(st)
    rid = _by_qid(st, "process")["id"]
    res = recommend.act(st, rid, "done")
    assert res["status"] == "in_progress" and "ещё на месте" in res["message"]


def test_fixing_answer_autocloses_with_nod_not_praise():
    st = _fresh("Автозакрытие")
    recommend.view(st)
    rid = _by_qid(st, "process")["id"]
    state.set_answer(st, 1, "process", "answered", "Проверять договоры перед подписанием")
    v = recommend.view(st)
    meta = st["recs"][rid]
    assert meta["status"] == "done" and meta["auto"] and meta["praise"] in recommend.NODS
    assert any(d["id"] == rid for d in v["done_feed"])


def test_condition_returns_reopens():
    st = _fresh("Возврат")
    recommend.view(st)
    rid = _by_qid(st, "process")["id"]
    state.set_answer(st, 1, "process", "answered", "Идея")
    recommend.view(st)
    st["answers"]["1"].pop("process")
    recommend.view(st)
    assert st["recs"][rid]["status"] == "in_progress"


def test_hard_fix_gets_praise_simple_gets_nod():
    assert recommend._ack({"effort": 3, "source": "attack"}, "r-1") in recommend.PRAISE_HARD
    assert recommend._ack({"effort": 2, "source": "consistency"}, "r-2") in recommend.PRAISE_HARD
    assert recommend._ack({"effort": 1, "source": "question"}, "r-3") in recommend.NODS
    for n in recommend.NODS:
        assert len(n.split()) <= 2  # кивок, а не речь


def test_self_checked_source_closes_on_mark():
    st = state.create("Отрасль", 3, domain=core.domains()[0]["id"])
    rec = next(r for r in recommend.candidates(st).values() if r["source"] == "domain")
    res = recommend.act(st, rec["id"], "done")
    assert res["status"] == "done" and res["message"] in recommend.NODS


def test_dismiss_needs_reason_and_defer_hides_until_step():
    st = _fresh("Отложить")
    recommend.view(st)
    rid = _by_qid(st, "process")["id"]
    with pytest.raises(ValueError):
        recommend.act(st, rid, "dismiss")
    recommend.act(st, rid, "defer")
    assert st["recs"][rid]["defer_step"] == 2
    assert rid not in [i["id"] for i in recommend.view(st)["next"]]
    st["current_step"] = 2
    assert rid in [i["id"] for i in recommend.view(st)["plan"]["Ш1"]]
    recommend.act(st, rid, "dismiss", reason="процесс описан в регламенте")
    assert st["recs"][rid]["status"] == "dismissed" and st["recs"][rid]["reason"]
    with pytest.raises(ValueError):
        recommend.act(st, rid, "fly")


def _last_step_recs():
    last = core.step_ids()[-1]
    st = _fresh("После запуска")
    st["current_step"] = last
    v = recommend.view(st)
    caution = next(i for x in v["plan"].values() for i in x if not i.get("nav") and i["level"] != "warning")
    warning = next(i for x in v["plan"].values() for i in x if not i.get("nav") and i["level"] == "warning")
    nav = next(i for i in v["next"] if i.get("nav") == "primary")
    return st, caution, warning, nav


def test_defer_on_last_step_goes_to_after_launch_plan():
    """До 12.10.2026 «Отложить» на последнем шаге ничего не скрывало: defer_step == текущему, пункт возвращался сразу.
    Теперь — план после запуска с владельцем и стадией; в «Следующий шаг» не попадает, в план и документы — да."""
    st, rec, _, _ = _last_step_recs()
    with pytest.raises(ValueError, match="владельца"):
        recommend.act(st, rec["id"], "defer")
    with pytest.raises(ValueError, match="стадию"):
        recommend.act(st, rec["id"], "defer", reason="когда-нибудь", owner="аналитик")
    res = recommend.act(st, rec["id"], "defer", reason="человек в контуре", owner="аналитик процесса")
    assert res["status"] == "after_launch" and "человек в контуре" in res["message"]
    v = recommend.view(st)
    assert rec["id"] not in [i["id"] for i in v["next"]]
    assert rec["id"] not in [i["id"] for x in v["plan"].values() for i in x]
    al = next(i for i in v["after_launch"] if i["id"] == rec["id"])
    assert al["path"][0].startswith("В плане после запуска") and "аналитик процесса" in al["path"][0]
    assert al["path"][-1].startswith("Готово") and v["counts"]["after_launch"] == 1
    line = recommend.after_launch_lines(st)[0]
    assert "человек в контуре" in line and "аналитик процесса" in line
    from app import artifacts, defense
    assert "## План после запуска" in defense.for_project(st)["markdown"]
    assert "# План после запуска" in artifacts.passport(st)["markdown"]
    # закрывается отметкой владельца — работа идёт вне copilot
    assert recommend.act(st, rec["id"], "done")["status"] == "done"


def test_defer_refuses_warning_on_last_step_and_navigation_with_path():
    st, _, warn, nav = _last_step_recs()
    with pytest.raises(ValueError, match="Реестр рисков") as e:
        recommend.act(st, warn["id"], "defer", reason="тень", owner="аналитик")
    assert "отложить нельзя" in str(e.value)
    item = next(i for x in recommend.view(st)["plan"].values() for i in x if i["id"] == warn["id"])
    assert any("примите риском" in p for p in item["path"]) and item["path"][-1].startswith("Готово")
    with pytest.raises(ValueError, match="ход по маршруту"):
        recommend.act(st, nav["id"], "defer")


def test_defer_before_last_step_names_return_and_earlier_path():
    st = _fresh("Отложить с путём")
    recommend.view(st)
    rid = _by_qid(st, "process")["id"]
    msg = recommend.act(st, rid, "defer")["message"]
    assert "до шага Ш2" in msg and "Закрыть раньше" in msg
    item = next(i for i in recommend.view(st)["plan"]["Ш1"] if i["id"] == rid)
    assert item["path"][0].startswith("Отложено до шага Ш2") and item["path"][-1].startswith("Готово")


def test_enrich_goes_through_guard_and_is_saved():
    st = _fresh("План")
    rid = _by_qid(st, "process")["id"]
    r = FakeRouter('{"why_here": "Проект только рождается", "plan": ["Опишите процесс одной фразой"], '
                   '"example": "Проверка договоров перед подписанием", "praise": ""}')
    out = recommend.enrich(st, rid, router=r)
    assert r.calls == 1 and out["plan"] == ["Опишите процесс одной фразой"]
    assert st["recs"][rid]["enriched"]["why_here"] == "Проект только рождается"


def test_enrich_bad_format_is_router_error():
    from app.router import RouterError
    st = _fresh("План")
    rid = _by_qid(st, "process")["id"]
    with pytest.raises(RouterError):
        recommend.enrich(st, rid, router=FakeRouter("просто текст без JSON"))


def test_api_recs_flow():
    st = _fresh("API рек")
    state.save(st, "тест")
    pid = st["id"]
    v = c.get(f"/api/projects/{pid}/recs").json()
    rid = v["next"][0]["id"]
    vers = len(c.get(f"/api/projects/{pid}/versions").json())
    c.get(f"/api/projects/{pid}/recs")
    assert len(c.get(f"/api/projects/{pid}/versions").json()) == vers  # просмотр без изменений версий не плодит
    r = c.post(f"/api/projects/{pid}/recs/{rid}", json={"action": "start"}).json()
    assert r["status"] == "in_progress" and r["recs"]["next"][0]["id"] == rid
    assert c.post(f"/api/projects/{pid}/recs/{rid}", json={"action": "dismiss"}).status_code == 400
    assert c.post(f"/api/projects/{pid}/recs/r-nope", json={"action": "start"}).status_code == 400
