"""Находки прогона тремя агентами (09.10.2026): каждая закреплена тестом, который её ловит."""
import json
import os
import re
import subprocess
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import attacks, config, core, defense, prototypes, readiness, router, state, target, workspace
from app.main import app

c = TestClient(app)


# ---------- ядро ----------
def test_choice_options_not_split_by_comma():
    assert "Агент, который сам расширяет набор инструментов" in core.question(2, "agency_level")["options"]
    assert "Один вызов модели с примерами, без цикла" in core.question(2, "agency_level")["options"]
    assert "Частично — работает, но только в логе" in core.question(4, "prototype_run")["options"]
    for s in core.steps():
        for q in s["questions"]:
            for o in q.get("options", []):
                assert not re.match(r"^[а-яa-z]", o), (q["id"], o)  # обрывок варианта начинается со строчной
            for o in q.get("risky_options", []):
                assert o in q["options"], (q["id"], o)


def test_new_prod_questions_present():
    ids = {(s["id"], q["id"]) for s in core.steps() for q in s["questions"]}
    # было: шаги 7, 9, 10, 12 маршрута из 12; стало: Ш3, Ш4, Ш6; плюс новые вопросы размена и обратной связи
    assert {(3, "concurrency"), (4, "audit_log"), (6, "input_drift"), (6, "restore_tested"), (4, "key_rotation"),
            (2, "tradeoff"), (6, "user_feedback")} <= ids


def test_risky_option_is_risk_and_step_not_ready():
    st = state.create("Рискованный вариант", 1)
    state.set_answer(st, 4, "prototype_run", "answered", "Нет ещё")
    rs = [r for r in readiness.risks(st) if r["qid"] == "prototype_run"]
    assert rs and rs[0]["reason"] == "Выбран рискованный вариант" and rs[0]["level"] == "warning"


def test_multi_rejects_unknown_option():
    st = state.create("Мульти", 5)
    with pytest.raises(ValueError, match="нет такого варианта"):
        state.set_answer(st, 5, "attack_selection", "answered", ["Взлом соседа"])


def test_needs_detail_risk_text_matches_state():
    st = state.create("Текст риска", 3)
    state.set_answer(st, 3, "pattern", "answered", "Router — маршрутизатор")
    state.set_senior_review(st, 3, "pattern", {"verdict": "needs_detail", "follow_up": "Почему не if-ветка?"})
    r = next(r for r in readiness.risks(st) if r["qid"] == "pattern")
    assert "Почему не if-ветка?" in r["text"] and "не выбран" not in r["text"]


def test_attacks_not_run_is_risk():
    st = state.create("Без испытаний", 5)
    state.set_answer(st, 5, "attack_target", "answered", "стенд")
    assert any(r["id"] == "run:attacks" and r["step"] == 5 for r in readiness.risks(st))
    st2 = state.create("Только качество", 5)
    state.set_answer(st2, 5, "regression", "answered", "один набор до и после")
    assert not any(r["id"] == "run:attacks" for r in readiness.risks(st2))  # отрезок «Атаки» не начат


# ---------- защита ----------
def test_reconcile_whole_numbers_in_own_source():
    f = {"loss": [{"text": "теряем 360 минут в неделю", "ref": "Ш2 · потеря сейчас"}],
         "attacks": [{"text": "пройдено 3 из 4", "ref": "Ш5 · протокол"}]}
    slides = [{"n": 1, "title": "Теряем 360 минут в неделю", "title_ref": "Ш2 · потеря сейчас",
               "lines": ["пройдено 3 из 7 (Ш2 · потеря сейчас)"]}]
    rows = defense.reconcile(slides, f)
    by = {(r["number"], r["source"]): r["match"] for r in rows}
    assert by[("360", "Ш2 · потеря сейчас")] is True
    assert by[("3", "Ш2 · потеря сейчас")] is False, "«3» не должно находиться внутри «360» и в чужом источнике"
    assert len(rows) == len({(r["slide"], r["number"], r["source"]) for r in rows}), "дубли в сверке"


def test_go_blocked_by_failed_attack():
    st = state.create("Защита с находкой", 6)
    state.set_answer(st, 2, "loss_now", "answered", "60 минут в день")
    state.add_risk(st, 5, "Испытание «Повтор» не пройдено", "warning", "attack")
    st["attack_runs"] = [{"ts": state.now(), "target": "x", "summary": {"repeat": "failed"}}]
    d = defense.for_project(st)
    assert d["go"]["verdict"] == "no_go" and d["go"]["blockers"]


def test_defense_markdown_dates_ddmmyyyy():
    st = state.create("Даты", 1)
    md = defense.for_project(st)["markdown"]
    assert not re.search(r"\d{4}-\d{2}-\d{2}", md)
    assert re.search(r"Собрано \d{2}\.\d{2}\.\d{4} \d{2}:\d{2}", md)


# ---------- безопасность адреса агента ----------
def test_headers_env_only_agent_prefix():
    with pytest.raises(ValueError, match="AGENT_"):
        target.normalize({"url": "http://127.0.0.1:8100/run", "headers_env": "OPENROUTER_API_KEY"})
    with pytest.raises(ValueError, match="AGENT_"):
        target.normalize({"url": "http://evil.example/run", "headers_env": "PATH"})
    assert target.normalize({"url": "http://127.0.0.1:8100/run", "headers_env": "AGENT_TOKEN"})["headers_env"] == "AGENT_TOKEN"


def test_target_api_rejects_secret_env():
    pid = c.post("/api/projects", json={"name": "Утечка"}).json()["project"]["id"]
    r = c.put(f"/api/projects/{pid}/target", json={"url": "http://evil.example/run", "headers_env": "OPENROUTER_API_KEY"})
    assert r.status_code == 400


def test_huge_agent_response_is_capped():
    big = b"x" * (target.MAX_RESPONSE_BYTES + 500_000)
    tr = httpx.MockTransport(lambda req: httpx.Response(200, content=big))
    r = target.call({"url": "http://127.0.0.1:8100/run"}, "q", transport=tr)
    assert r["ok"] is False and "МБ" in r["error"] and len(r["raw"]) <= 4000


def test_attack_on_idle_agent_is_inconclusive():
    st = state.create("Не доказано", 5)
    st["target"] = target.normalize({"url": "http://127.0.0.1:8100/run"})
    tr = httpx.MockTransport(lambda req: httpx.Response(200, json={"answer": "", "status": "stopped_by_limit"}))
    armed = {"selected": ["injection", "leak"], "target": st["target"]}
    run = attacks.execute(armed, transport=tr)
    assert {r["id"]: r["verdict"] for r in run["results"]} == {"injection": "inconclusive", "leak": "inconclusive"}
    attacks.record(st, run)
    assert any("не доказано" in r["text"] for r in st["risks_extra"])


def test_attack_protocol_has_no_control_chars():
    st = state.create("Протокол", 5)
    run = {"ts": state.now(), "target": "http://x", "results": [{"id": "garbage", "title": "Мусор", "verdict": "passed",
           "problems": [], "level": "caution", "calls": [{"payload": "\x00\x01abc", "status_code": 200, "seconds": 0.1,
                                                          "status": "", "answer": "a\x00b", "error": ""}]}]}
    path = attacks.write_protocol(st, run)
    text = open(path, encoding="utf-8").read()
    assert "\x00" not in text and not re.search(r"\d{4}-\d{2}-\d{2}T", text)


# ---------- хранение ----------
def test_list_survives_broken_project():
    ok = state.create("Целый", 1)
    bad = state.create("Испорченный", 1)
    (state.project_dir(bad["id"]) / "state.json").write_text("[]", encoding="utf-8")
    items = c.get("/api/projects")
    assert items.status_code == 200
    ids = {p["id"]: p for p in items.json()}
    assert ok["id"] in ids and ids[bad["id"]].get("broken")


def test_dead_owner_lock_released_immediately():
    st = state.create("Мёртвый замок", 1)
    lock = state.project_dir(st["id"]) / "state.lock"
    p = subprocess.Popen(["python", "-c", "pass"])
    p.wait()
    lock.write_text(str(p.pid), encoding="utf-8")  # замок процесса, который уже умер; файл свежий
    t0 = time.monotonic()
    with state.locked(st["id"], wait_sec=5):
        pass
    assert time.monotonic() - t0 < 1.0


def test_no_folder_for_unknown_project():
    r = c.post("/api/projects/zzz-nonexistent/answer", json={"step": 1, "qid": "process", "status": "unknown"})
    assert r.status_code == 404
    assert not (config.projects_dir() / "zzz-nonexistent").exists()


def test_bad_bodies_are_400_not_500():
    pid = c.post("/api/projects", json={"name": "Тела"}).json()["project"]["id"]
    for body in ({"qid": "process"}, {"step": "abc", "qid": "process"}, {"step": 1, "qid": "process", "note": {"x": 1}}):
        assert c.post(f"/api/projects/{pid}/answer", json=body).status_code == 400, body
    assert c.post("/api/projects", json={"name": "x", "start_step": 99}).status_code == 400
    assert c.post(f"/api/projects/{pid}/restore", json={"version": "../x"}).status_code == 400


def test_answer_size_limit():
    st = state.create("Размер", 1)
    with pytest.raises(ValueError, match="длиннее"):
        state.set_answer(st, 1, "process", "answered", "x" * (state.MAX_TEXT_CHARS + 1))


def test_versions_and_list_dates_human():
    st = state.create("Даты версий", 1)
    v = state.versions(st["id"])[0]
    assert re.match(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}$", v["label"])
    assert re.match(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$", state.list_projects()[0]["updated_text"])


def test_slug_has_no_double_hyphen():
    assert "--" not in state.slugify("Сверка счетов поставщиков с заказами в учётной системе")


def test_parallel_read_write_no_500():
    pid = c.post("/api/projects", json={"name": "Чтение и запись"}).json()["project"]["id"]
    codes = []
    qids = [q["id"] for q in core.step(1)["questions"] if q["type"] == "text"]

    def writer(i):
        r = c.post(f"/api/projects/{pid}/answer", json={"step": 1, "qid": qids[i % len(qids)], "status": "answered",
                                                       "value": f"ответ {i}", "review": False})
        codes.append(r.status_code)

    def reader():
        codes.append(c.get(f"/api/projects/{pid}").status_code)
        codes.append(c.get("/api/projects").status_code)

    ts = [threading.Thread(target=writer, args=(i,)) for i in range(30)] + [threading.Thread(target=reader) for _ in range(30)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert codes.count(500) == 0, codes


# ---------- роутер ----------
def test_truncated_stream_is_error():
    body = b'data: {"choices":[{"delta":{"content":"{\\"verdict\\": \\"acc"}}]}\n\n'
    tr = httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    cfg = router.load_models()
    cfg["roles"]["senior"] = ["lmstudio-local"]
    with pytest.raises(router.RouterError, match="оборван"):
        router.Router(cfg, transport=tr).chat("senior", "s", [{"role": "user", "content": "x"}])


def test_byte_dribble_respects_total_timeout():
    def gen():
        for ch in b'data: {"choices":[{"delta":{"content":"aaaaaaaaaaaaaaaaaaaa"}}]}':
            time.sleep(0.05)
            yield bytes([ch])
    tr = httpx.MockTransport(lambda req: httpx.Response(200, content=gen()))
    cfg = router.load_models()
    cfg["roles"]["senior"] = ["lmstudio-local"]
    cfg["timeout_sec"] = 1
    t0 = time.monotonic()
    with pytest.raises(router.RouterError):
        router.Router(cfg, transport=tr).chat("senior", "s", [{"role": "user", "content": "x"}])
    assert time.monotonic() - t0 < 3


def test_budget_reserve_blocks_parallel_overspend(fake_llm):
    cfg = router.load_models()
    cfg["roles"]["senior"] = ["openrouter-opus"]
    cfg["budget_usd"] = 0.05  # оценка одного вызова на 1000 токенов ответа ≈ 0,02 $ — влезает два
    srv = fake_llm("ok")
    orig = srv.__call__

    def slow(request):
        time.sleep(0.3)
        return orig(request)
    tr = httpx.MockTransport(slow)
    ok, refused = [], []

    def call():
        try:
            router.Router(cfg, transport=tr).chat("senior", "s", [{"role": "user", "content": "x"}], max_tokens=1000)
            ok.append(1)
        except router.BudgetExceeded:
            refused.append(1)
    ts = [threading.Thread(target=call) for _ in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) <= 2 and len(refused) >= 8


def test_broken_models_yaml_screen_opens():
    config.models_file().write_text("endpoints: [oops\n", encoding="utf-8")
    r = c.get("/api/models")
    assert r.status_code == 200 and "error" in r.json()
    pid = c.post("/api/projects", json={"name": "Битый роутер"}).json()["project"]["id"]
    r = c.post(f"/api/projects/{pid}/answer", json={"step": 1, "qid": "process", "status": "answered", "value": "Собрать протокол"})
    assert r.status_code == 200 and r.json()["review_error"]


def test_models_reject_top_level_key_and_inf_budget():
    cfg = router.load_models()
    assert router.validate_models({**cfg, "api_key": "sk-x"})
    assert router.validate_models({**cfg, "budget_usd": "1e309"})
    assert router.validate_models({**cfg, "endpoints": {**cfg["endpoints"], "f": {**cfg["endpoints"]["ollama-local"], "base_url": "file:///etc"}}})


def test_public_view_hides_handwritten_key():
    cfg = router.load_models()
    cfg["endpoints"]["ollama-local"]["api_key"] = "sk-handwritten-000000000000"
    assert "sk-handwritten" not in json.dumps(router.public_view(cfg))


def test_models_save_keeps_price_checked():
    cfg = router.load_models()
    router.save_models(cfg)
    assert router.load_models()["endpoints"]["openrouter-opus"]["price_checked"] == "09.10.2026"
    assert "price_checked — дата сверки" in config.models_file().read_text(encoding="utf-8")


# ---------- разработка Junior Pilot ----------
def test_words_in_readme_are_not_mechanisms(tmp_path):
    d = tmp_path / "words"
    d.mkdir()
    (d / "README.md").write_text("max_steps stopped_by_limit retry очередь cost_usd idempotency", encoding="utf-8")
    (d / "main.py").write_text("# max_steps = 10\nprint('hi')\n", encoding="utf-8")
    f = {x["check"]: x["level"] for x in workspace.run_checks(d, workspace.inventory(d))}
    assert f["limits"] != "ok" and f["limit_outcome"] != "ok" and f["retry"] != "ok"


def test_own_template_retries_recognized():
    tpl = config.TEMPLATES_DIR / "python_agent"
    f = {x["check"]: x["level"] for x in workspace.run_checks(tpl, workspace.inventory(tpl))}
    assert f["retry"] == "ok" and f["limits"] == "ok" and f["idempotency"] == "ok"


@pytest.mark.skipif(os.name != "nt", reason="junction — Windows")
def test_junction_into_copilot_not_followed(tmp_path):
    d = tmp_path / "junior"
    d.mkdir()
    (d / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(d / "link"), str(config.ROOT / "app")], capture_output=True, check=True)
    try:
        paths = [x["path"] for x in workspace.inventory(d)]
        assert paths == ["a.py"]
    finally:
        os.rmdir(d / "link")


def test_unc_path_rejected():
    with pytest.raises(ValueError, match="сетевые"):
        workspace.validate_path(r"\\127.0.0.1\c$")


@pytest.mark.parametrize("secret", [
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
    "SLACK=xoxb-123456789012-abcdefghijkl",
    "postgres://admin:SuperSecret123@db.local:5432/x",
    "key = AIzaSyA1234567890abcdefghijklmnopqrstuvw",
    '{"api_key": "abcd1234efgh5678ijkl"}',
    "password: plainpassword123",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAsecretbody\n-----END RSA PRIVATE KEY-----",
])
def test_mask_more_secret_formats(secret):
    masked, n = workspace.mask_secrets(secret)
    assert n >= 1
    for frag in ("eyJzdWIi", "xoxb-1234", "SuperSecret123", "AIzaSyA123", "abcd1234efgh", "plainpassword123", "secretbody"):
        assert frag not in masked


def test_secret_detection_ignores_function_calls(tmp_path):
    d = tmp_path / "calls"
    d.mkdir()
    (d / "a.py").write_text("token = get_token_from_vault()\n", encoding="utf-8")
    assert not any(x["check"] == "secret_in_code" for x in workspace.run_checks(d, workspace.inventory(d)))


# ---------- прототипы ----------
def test_n8n_schedule_passes_query_and_parses_cron():
    st = state.create("Расписание", 3)
    state.set_answer(st, 3, "trigger", "answered", "Каждые 30 минут с 8:00 до 19:00 по рабочим дням")
    state.set_answer(st, 2, "core_step", "answered", "Сверить счета")
    wf = prototypes.n8n_workflow(st)
    sched = next(n for n in wf["nodes"] if n["type"].endswith("scheduleTrigger"))
    assert sched["parameters"]["rule"]["interval"][0]["expression"] == "*/30 8-19 * * 1-5"
    setn = next(n for n in wf["nodes"] if n["type"].endswith(".set"))
    assert "Сверить счета" in setn["parameters"]["assignments"]["assignments"][0]["value"]
    assert wf["connections"][sched["name"]]["main"][0][0]["node"] == setn["name"]


def test_skeleton_respects_perimeter_decision():
    st = state.create("Периметр", 4)
    state.set_answer(st, 2, "secret_kind", "answered", "Тайна в данных — гибрид с фильтром персональных данных перед внешней моделью")
    assert all("openrouter" not in e["base_url"] for e in prototypes.llm_endpoints(st))
    state.set_answer(st, 2, "secret_kind", "answered", "Тайны нет — всё по API")
    assert any("openrouter" in e["base_url"] for e in prototypes.llm_endpoints(st))


def test_diagram_shows_approval_before_write_and_russian_limits():
    st = state.create("Утверждение", 3)
    state.set_answer(st, 3, "control_table", "answered", [["Провести оплату", "необратимый", "утвердить", "главный бухгалтер", "деньги"]])
    state.set_answer(st, 4, "limits_table", "answered", [["вызовы", "14", "", "", ""], ["деньги", "0,3 $", "", "", ""]])
    m = prototypes.agent_diagram(st)["mermaid"]
    assert "Утверждает до записи: главный бухгалтер" in m
    assert "шагов 14" in m and "max_" not in m and "cost_usd" not in m
