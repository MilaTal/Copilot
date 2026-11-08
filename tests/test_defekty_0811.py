"""Дефекты прогона 08.11.2026: состояние проверки, вердикт атак, каркас защиты, вывод шага,
снятые рекомендации, архив проекта, нулевая цена, признак «маршрут пройден»."""
import json

from fastapi.testclient import TestClient

from app.main import app

c = TestClient(app)


def _project(name="Дефекты 08.11"):
    return c.post("/api/projects", json={"name": name}).json()["project"]["id"]


def test_review_error_survives_reload(monkeypatch):
    """Сбой проверки Senior Pilot остаётся в ответе: после перезагрузки видно, что проверки не было."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from app import router
    cfg = router.load_models()
    cfg["roles"]["senior"] = ["openrouter-opus"]
    router.save_models(cfg)
    pid = _project("Сбой проверки")
    r = c.post(f"/api/projects/{pid}/answer", json={"step": 1, "qid": "process", "status": "answered",
                                                    "value": "Собрать протокол"}).json()
    assert r["review_error"]
    v = c.get(f"/api/projects/{pid}").json()
    rec = v["project"]["answers"]["1"]["process"]
    assert rec["senior"] is None
    assert rec["senior_error"] and "OPENROUTER_API_KEY" in rec["senior_error"]
    # новый ответ без проверки не тащит старую ошибку
    c.post(f"/api/projects/{pid}/answer", json={"step": 1, "qid": "process", "status": "skipped"})
    v = c.get(f"/api/projects/{pid}").json()
    assert "senior_error" not in v["project"]["answers"]["1"]["process"]


def test_attacks_unreachable_is_not_a_weakness():
    """Стенд не отвечает — вердикт «не проверено», а не «нужно усилить»."""
    from app import attacks, core
    a = next(x for x in core.attacks() if not x.get("manual"))
    results = [{"answer": "", "status": "", "raw": "", "error": "ConnectError: нет соединения",
                "status_code": None, "seconds": 0.0}]
    verdict, problems = attacks._evaluate(a, results)
    assert verdict == "unreachable"
    assert "стенд недоступен" in problems[0]


def test_defense_frame_text_when_blockers():
    """Каркас собран — предупреждение говорит «собрано, перед показом закройте», а не «пока рано собирать»."""
    from app import defense
    st = {"answers": {}, "risks_extra": [], "project": {}}
    d = defense.build({}, "Проверка текста", blockers=["не пройдены испытания атак"])
    assert d["slides"]
    assert d["go"]["verdict"] == "no_go"
    assert d["go"]["text"].startswith("Каркас собран")


def test_step1_synthesis_is_labeled():
    """Вывод Ш1 не склеивает ответы с шаблонной фразой в неверном порядке: у каждой части свой ярлык."""
    from app import core, mentor, state
    st = state.create("Вывод шага")
    state.set_answer(st, 1, "who_needs", "answered", "Пользоваться будут заявители")
    d = mentor.step_synthesis_draft(st, 1)
    assert d["draft"].startswith("Кто сталкивается:")
    assert "Сейчас Пользоваться" not in d["draft"]


def test_dismissed_is_counted_and_visible():
    """Снятая рекомендация считается и остаётся в плане с причиной."""
    pid = _project("Снятая рекомендация")
    c.post(f"/api/projects/{pid}/answer", json={"step": 1, "qid": "process", "status": "skipped"})
    v = c.get(f"/api/projects/{pid}/recs").json()
    assert v["plan"], "ожидались рекомендации по пропущенному ответу"
    rid = next(iter(v["plan"].values()))[0]["id"]
    r = c.post(f"/api/projects/{pid}/recs/{rid}", json={"action": "dismiss", "reason": "не про этот проект"})
    assert r.status_code == 200
    v = c.get(f"/api/projects/{pid}/recs").json()
    assert v["counts"]["dismissed"] >= 1
    assert any("не про этот проект" == x["reason"] for x in v["dismissed_feed"])


def test_archive_project_moves_and_hides():
    """Проект уходит в архив: из списка исчезает, папка сохраняется."""
    pid = _project("В архив")
    r = c.delete(f"/api/projects/{pid}")
    assert r.status_code == 200 and r.json()["archived"]
    ids = [p["id"] for p in c.get("/api/projects").json()]
    assert pid not in ids
    assert c.delete("/api/projects/net-takogo-1234").status_code == 404
    assert c.delete("/api/projects/нет-такого").status_code == 400  # негодный идентификатор


def test_zero_price_warning_on_models_screen():
    """Нулевая цена у платной точки показывается предупреждением."""
    from app import router
    cfg = router.load_models()
    cfg["endpoints"]["openrouter-opus"]["price_in"] = 0
    cfg["endpoints"]["openrouter-opus"]["price_out"] = 0
    router.save_models(cfg)
    d = c.get("/api/models").json()
    assert any("openrouter-opus" in w and "цена 0" in w for w in d["warnings"])


def test_projects_list_marks_route_done_field():
    """У карточки проекта есть признак пройденного маршрута и число закрытых шагов."""
    pid = _project("Маршрут")
    p = next(x for x in c.get("/api/projects").json() if x["id"] == pid)
    assert p["route_done"] is False
    assert p["steps_total"] == 6 and p["steps_closed"] == 0
