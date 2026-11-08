"""Гонки: замок стоит на данных, а не на кнопке. Медленная проверка Senior Pilot не затирает соседние ответы."""
import threading
import time

from fastapi.testclient import TestClient

from app import main, senior, state

c = TestClient(main.app)


def test_slow_review_does_not_lose_parallel_answer(monkeypatch):
    def slow_review(st, step_id, qid, router=None):
        time.sleep(0.8)
        return {"verdict": "accepted", "comment": "ок", "follow_up": "", "risks": [], "model": "fake", "cost_usd": 0}
    monkeypatch.setattr(senior, "review_answer", slow_review)
    pid = c.post("/api/projects", json={"name": "Гонка"}).json()["project"]["id"]
    slow = threading.Thread(target=lambda: c.post(f"/api/projects/{pid}/answer", json={
        "step": 1, "qid": "process", "status": "answered", "value": "Собрать протокол"}))
    slow.start()
    time.sleep(0.2)
    r = c.post(f"/api/projects/{pid}/answer", json={"step": 1, "qid": "if_nothing", "status": "unknown"})
    assert r.status_code == 200
    slow.join()
    a = state.load(pid)["answers"]["1"]
    assert a["if_nothing"]["status"] == "unknown", "быстрый ответ потерян — гонка вернулась"
    assert a["process"]["status"] == "answered" and a["process"]["senior"]["comment"] == "ок"


def test_review_not_applied_to_changed_answer(monkeypatch):
    pid = c.post("/api/projects", json={"name": "Устаревшая проверка"}).json()["project"]["id"]

    def slow_review(st, step_id, qid, router=None):
        time.sleep(0.6)
        return {"verdict": "needs_detail", "comment": "мало", "follow_up": "сколько?", "risks": ["r"], "model": "f", "cost_usd": 0}
    monkeypatch.setattr(senior, "review_answer", slow_review)
    t = threading.Thread(target=lambda: c.post(f"/api/projects/{pid}/answer", json={
        "step": 2, "qid": "loss_now", "status": "answered", "value": "много"}))
    t.start()
    time.sleep(0.2)
    c.post(f"/api/projects/{pid}/answer", json={"step": 2, "qid": "loss_now", "status": "answered",
                                               "value": "60 минут в день", "review": False})
    t.join()
    rec = state.load(pid)["answers"]["2"]["loss_now"]
    assert rec["value"] == "60 минут в день" and rec["status"] == "answered" and rec["senior"] is None


def test_many_parallel_answers_all_saved():
    pid = c.post("/api/projects", json={"name": "Шторм"}).json()["project"]["id"]
    qids = ["process", "why_company", "who_needs", "success_picture", "if_nothing", "decision_maker"]
    threads = [threading.Thread(target=lambda q=q: c.post(f"/api/projects/{pid}/answer", json={
        "step": 1, "qid": q, "status": "answered", "value": f"ответ {q} 1", "review": False})) for q in qids]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert set(state.load(pid)["answers"]["1"]) == set(qids)
    assert not (state.project_dir(pid) / "state.lock").exists()


def test_stale_lockfile_is_recovered():
    st = state.create("Брошенный замок", 1)
    lock = state.project_dir(st["id"]) / "state.lock"
    lock.write_text("99999")
    import os
    old = time.time() - state.LOCK_STALE_SEC - 5
    os.utime(lock, (old, old))
    with state.locked(st["id"]):
        pass
    assert not lock.exists()
