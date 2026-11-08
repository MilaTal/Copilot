"""Третий круг: находки повторного прогона агентами и живого прогона Senior Pilot на настоящей модели (09.10.2026)."""
import json
import subprocess
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, defense, router, senior, state, workspace
from app.main import app

c = TestClient(app)


def sse_lines(text, finish="stop"):
    body = f'data: {json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False)}\n\n'
    body += f'data: {json.dumps({"choices": [{"delta": {}, "finish_reason": finish}]})}\n\n'
    body += "data: [DONE]\n\n"
    return body.encode("utf-8")


def router_with(text, finish="stop"):
    cfg = router.load_models()
    cfg["roles"]["senior"] = ["lmstudio-local"]
    cfg["roles"]["reviewer"] = ["lmstudio-local"]
    tr = httpx.MockTransport(lambda req: httpx.Response(200, content=sse_lines(text, finish)))
    return router.Router(cfg, transport=tr)


def test_senior_prose_without_json_is_not_accepted():
    st = state.create("Не JSON", 1)
    state.set_answer(st, 1, "process", "answered", "Собрать протокол")
    with pytest.raises(router.RouterError, match="не по формату"):
        senior.review_answer(st, 1, "process", router=router_with("Я не буду отвечать JSON, ответ хороший."))


def test_review_material_truncated_is_error_not_raw_text(tmp_path):
    d = tmp_path / "j"
    d.mkdir()
    (d / "a.py").write_text("x = 1\n", encoding="utf-8")
    st = state.create("Обрезка", 4)
    st["workspace"] = {"path": str(d), "cloud_allowed": True}
    workspace.scan(st)
    m = workspace.material_for_model(st)
    with pytest.raises(router.RouterError, match="обрезан"):
        senior.review_material(st, m, router=router_with('```json\n{"summary": "начало', finish="length"))


def test_review_answer_retries_when_truncated():
    """Ответ с образцом длиннее: обрезанный пределом длины JSON — повтор с запасом, а не ошибка."""
    st = state.create("Обрезанная проверка", 1)
    state.set_answer(st, 2, "loss_now", "answered", "много времени")
    replies = iter([sse_lines('{"verdict": "needs_detail", "comment": "Хорошо, что', finish="length"),
                    sse_lines('{"verdict": "needs_detail", "comment": "Есть боль, нет числа.", '
                              '"follow_up": "Сколько минут на счёт? Образец: «40 минут × 120 счетов в неделю»", "risks": []}')])
    cfg = router.load_models()
    cfg["roles"]["senior"] = ["lmstudio-local"]
    rt = router.Router(cfg, transport=httpx.MockTransport(lambda req: httpx.Response(200, content=next(replies))))
    rv = senior.review_answer(st, 2, "loss_now", router=rt)
    assert rv["verdict"] == "needs_detail" and "Образец" in rv["follow_up"]


def test_real_senior_answer_with_inner_quotes_parses():
    """Настоящий ответ Claude Opus 5.5 из журнала сбоев: прямые кавычки внутри образца ответа."""
    from pathlib import Path
    rec = json.loads((Path(__file__).parent / "fixture_senior_quotes.jsonl").read_text(encoding="utf-8").splitlines()[0])
    d = router.extract_json(rec["text"])
    assert d["verdict"] == "needs_detail" and '"сверка счёта с заказом"' in d["follow_up"] and len(d["risks"]) == 3


def test_repair_keeps_valid_json_intact():
    src = '{"a": "x, y", "b": ["1", "2"], "c": {"d": "e"}, "f": "сказал \\"да\\", потом ушёл"}'
    assert router.extract_json(src) == {"a": "x, y", "b": ["1", "2"], "c": {"d": "e"}, "f": 'сказал "да", потом ушёл'}


def test_router_marks_length_truncation():
    r = router_with("кусок", finish="length").chat("senior", "s", [{"role": "user", "content": "x"}])
    assert r.truncated is True


def test_models_raw_view_masks_handwritten_key():
    config.models_file().write_text("api_key: sk-or-v1-FAKEFAKEFAKEFAKEFAKEFAKE\nendpoints: {}\n", encoding="utf-8")
    body = c.get("/api/models").text
    assert "FAKEFAKE" not in body and "error" in body


def test_docstring_words_are_not_mechanisms(tmp_path):
    d = tmp_path / "doc"
    d.mkdir()
    (d / "main.py").write_text('"""max_steps, stopped_by_limit, retries, dedup, cost_usd, manual_queue"""\nprint(1)\n',
                               encoding="utf-8")
    f = {x["check"]: x["level"] for x in workspace.run_checks(d, workspace.inventory(d))}
    assert all(f[k] != "ok" for k in ("limits", "limit_outcome", "retry", "idempotency", "cost_log", "queue"))


def test_dates_are_single_numbers_in_reconcile():
    assert defense._numbers("пилот с 03.11.2026 по 28.11.2026, 2 человека") == ["03.11.2026", "28.11.2026", "2"]


def test_quote_shows_the_number():
    src = "Теряем 140 000 руб в месяц и 14 часов в неделю; 3 ошибки на 360 заявок, доля 17,5 % от всех заявок месяца"
    q = defense._quote_around(src, "17,5")
    assert "17,5" in q and len(q) <= 90


def test_api_500s_now_4xx():
    pid = c.post("/api/projects", json={"name": "Кривые запросы"}).json()["project"]["id"]
    assert c.get("/api/projects/BAD!/versions").status_code == 404
    assert c.get("/api/projects/AB/versions").status_code == 404
    assert c.get(f"/api/projects/{pid}/debrief/99").status_code == 404
    assert c.post(f"/api/projects/{pid}/build/99").status_code == 404
    assert c.post("/api/defense/documents", json={"text": 5}).status_code == 400
    vdir = state.project_dir(pid) / "versions"
    (vdir / "20260101-000000-000000.json").write_text("[]", encoding="utf-8")
    assert c.post(f"/api/projects/{pid}/restore", json={"version": "20260101-000000-000000"}).status_code == 400


def test_state_with_wrong_shape_is_broken():
    st = state.create("Форма", 1)
    f = state.project_dir(st["id"]) / "state.json"
    f.write_text(json.dumps({"id": 5, "name": None}), encoding="utf-8")
    item = next(p for p in state.list_projects() if p["id"] == st["id"])
    assert item.get("broken")


def test_cli_errors_are_short_and_risk_owner_checks_id():
    pid = state.create("CLI ошибки", 5)["id"]
    env = {**__import__("os").environ}
    run = lambda *a: subprocess.run([sys.executable, "-m", "app.cli", *a], cwd=config.ROOT, capture_output=True,
                                    text=True, encoding="utf-8", env=env)
    r = run("answer", pid, "5", "attack_selection", "--text", "Несуществующая атака")
    assert r.returncode == 2 and "Traceback" not in r.stderr and "нет такого варианта" in r.stderr
    r = run("answer", pid, "11", "attack_selection", "--text", "x")  # было: шаг 11 существовал; теперь шагов 6
    assert r.returncode == 2 and "Traceback" not in r.stderr and "нет шага 11" in r.stderr
    r = run("risk-owner", pid, "нет-такого", "--owner", "кто-то")
    assert r.returncode == 2 and "нет открытого риска" in r.stderr
    r = run("status", "zzz-nope")
    assert r.returncode == 2 and "Traceback" not in r.stderr


def test_repeated_senior_review_replaces_its_risks(monkeypatch):
    texts = iter(["Риск в одной формулировке", "Тот же риск, но другими словами"])

    def fake_review(st, step_id, qid, router=None):
        return {"verdict": "accepted", "comment": "ок", "follow_up": "", "risks": [next(texts)], "model": "m", "cost_usd": 0}
    monkeypatch.setattr(senior, "review_answer", fake_review)
    pid = c.post("/api/projects", json={"name": "Повторная проверка"}).json()["project"]["id"]
    for v in ("первый вариант ответа", "второй вариант ответа"):
        c.post(f"/api/projects/{pid}/answer", json={"step": 1, "qid": "process", "status": "answered", "value": v})
    open_senior = [r for r in state.load(pid)["risks_extra"] if r["source"] == "senior" and r["status"] == "open"]
    assert len(open_senior) == 1 and open_senior[0]["text"] == "Тот же риск, но другими словами"


def test_transient_provider_error_retried_on_same_endpoint(monkeypatch):
    monkeypatch.setattr(router, "RETRY_PAUSE_SEC", 0.01)
    calls = []
    err = b'data: {"error": {"code": 502, "message": "Network connection lost."}}\n\n'

    def handler(req):
        calls.append(req.url.host)
        if len(calls) == 1:
            return httpx.Response(200, content=err)
        return httpx.Response(200, content=sse_lines('{"verdict": "accepted", "comment": "ok", "follow_up": "", "risks": []}'))
    cfg = router.load_models()
    cfg["roles"]["senior"] = ["openrouter-opus", "lmstudio-local"]
    r = router.Router(cfg, transport=httpx.MockTransport(handler)).chat("senior", "s", [{"role": "user", "content": "x"}])
    assert r.endpoint == "openrouter-opus" and calls == ["openrouter.ai", "openrouter.ai"]


def test_non_retryable_error_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(400, text="bad request")
    cfg = router.load_models()
    cfg["roles"]["senior"] = ["lmstudio-local"]
    with pytest.raises(router.RouterError):
        router.Router(cfg, transport=httpx.MockTransport(handler)).chat("senior", "s", [{"role": "user", "content": "x"}])
    assert len(calls) == 1


def test_regression_endpoint():
    pid = c.post("/api/projects", json={"name": "Регрессия"}).json()["project"]["id"]
    assert c.get(f"/api/projects/{pid}/eval/regression").json() == {}
