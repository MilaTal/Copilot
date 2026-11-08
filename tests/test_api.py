"""Сквозь сервер: проект, ответ без модели, строгая сборка, ключи не уходят на экран, испытания без ключа 1 — отказ."""
from fastapi.testclient import TestClient

from app.main import app

c = TestClient(app)


def test_flow_create_answer_build_and_risks():
    v = c.post("/api/projects", json={"name": "API проект", "start_step": 3}).json()
    pid = v["project"]["id"]
    assert v["project"]["current_step"] == 3
    r = c.post(f"/api/projects/{pid}/answer", json={"step": 2, "qid": "core_step", "status": "answered",
                                                   "value": "Сопоставить фразы", "review": False})
    assert r.status_code == 200 and r.json()["review"] is None
    assert c.post(f"/api/projects/{pid}/build/2").status_code == 409
    assert c.post(f"/api/projects/{pid}/build/7").status_code == 404  # было: шагов 12 — теперь 6
    r = c.post(f"/api/projects/{pid}/answer", json={"step": 2, "qid": "write_action", "status": "skipped"}).json()
    assert any(x["qid"] == "write_action" and x["reason"] == "Сознательно опущено" for x in r["risks"])
    d = c.get(f"/api/projects/{pid}/debrief/2").json()
    assert "Дальше идти можно" in d["message"]


def test_senior_unavailable_does_not_lose_answer(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    from app import router
    cfg = router.load_models()
    cfg["roles"]["senior"] = ["openrouter-opus"]
    router.save_models(cfg)
    pid = c.post("/api/projects", json={"name": "Без модели"}).json()["project"]["id"]
    r = c.post(f"/api/projects/{pid}/answer", json={"step": 1, "qid": "process", "status": "answered", "value": "Собрать протокол"}).json()
    assert r["review_error"] and "OPENROUTER_API_KEY" in r["review_error"]
    assert r["project"]["answers"]["1"]["process"]["status"] == "answered"


def test_models_screen_shows_no_keys():
    body = c.get("/api/models").text
    assert "sk-or-v1" not in body and "key_present" in body


def test_attack_launch_without_arm_forbidden():
    pid = c.post("/api/projects", json={"name": "Атаки"}).json()["project"]["id"]
    c.put(f"/api/projects/{pid}/target", json={"url": "http://127.0.0.1:8100/run"})
    r = c.post(f"/api/projects/{pid}/attacks/launch", json={"token": "x", "typed_host": "127.0.0.1:8100", "not_prod": True})
    assert r.status_code == 403


def test_review_material_needs_confirmation():
    pid = c.post("/api/projects", json={"name": "Разбор"}).json()["project"]["id"]
    assert c.post(f"/api/projects/{pid}/workspace/review", json={}).status_code == 400


def test_index_and_meta():
    assert "Copilot" in c.get("/").text
    m = c.get("/api/meta").json()
    assert len(m["steps"]) == 6 and m["hints"] and m["attacks"]
    assert all(s["segments"] and s["slug"] for s in m["steps"])
