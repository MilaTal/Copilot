"""Роутер: поток, цена, запасная точка, потолок, периметр, ключи не видны."""
import json

import httpx
import pytest

from app import config
from app.router import (BudgetExceeded, Router, RouterError, extract_json, is_local_url, load_models, public_view,
                        save_models, validate_models)


def test_streamed_answer_and_cost(fake_llm):
    srv = fake_llm("привет")
    r = Router(transport=srv.transport).chat("senior", "sys", [{"role": "user", "content": "hi"}])
    assert r.text == "привет" and r.endpoint == "openrouter-opus"
    assert r.cost_usd == pytest.approx(120 / 1e6 * 4 + 30 / 1e6 * 20)
    log = config.cost_log().read_text(encoding="utf-8").splitlines()
    assert json.loads(log[-1])["ok"] is True


def test_fallback_to_next_endpoint(fake_llm, monkeypatch):
    cfg = load_models()
    cfg["roles"]["senior"] = ["openrouter-opus", "lmstudio-local"]
    srv = fake_llm("ответ запасной", fail_hosts={"openrouter.ai"})
    r = Router(cfg, transport=srv.transport).chat("senior", "s", [{"role": "user", "content": "x"}])
    assert r.endpoint == "lmstudio-local"


def test_missing_key_is_named_not_printed(fake_llm, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    cfg = load_models()
    cfg["roles"]["senior"] = ["openrouter-opus"]
    with pytest.raises(RouterError, match="OPENROUTER_API_KEY"):
        Router(cfg, transport=fake_llm("x").transport).chat("senior", "s", [{"role": "user", "content": "x"}])


def test_budget_stops_calls(fake_llm):
    """Потолок резервирует оценку вызова заранее: вызов, который не помещается в остаток, не уходит."""
    cfg = load_models()
    cfg["budget_usd"] = 0.05
    srv = fake_llm("x")
    r = Router(cfg, transport=srv.transport)
    r.chat("senior", "s", [{"role": "user", "content": "x"}], max_tokens=1000)  # оценка ≈ 0,02 $ — помещается
    with open(config.cost_log(), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": __import__("datetime").datetime.now().isoformat(), "cost_usd": 0.04, "ok": True}) + "\n")
    with pytest.raises(BudgetExceeded):
        r.chat("senior", "s", [{"role": "user", "content": "x"}], max_tokens=1000)
    assert len(srv.calls) == 1


def test_only_local_skips_cloud(fake_llm):
    cfg = load_models()
    srv = fake_llm("локально")
    r = Router(cfg, transport=srv.transport).chat("reviewer", "s", [{"role": "user", "content": "код"}], only_local=True)
    assert r.endpoint == "lmstudio-local"
    assert all(c.url.host in ("localhost", "127.0.0.1") for c in srv.calls)


def test_only_local_without_local_endpoint_fails(fake_llm):
    cfg = load_models()
    cfg["roles"]["reviewer"] = ["openrouter-opus"]
    with pytest.raises(RouterError, match="за периметром"):
        Router(cfg, transport=fake_llm("x").transport).chat("reviewer", "s", [{"role": "user", "content": "x"}], only_local=True)


def test_is_local_url():
    assert is_local_url("http://localhost:1234/v1")
    assert is_local_url("http://192.168.100.59:1234")
    assert is_local_url("http://10.0.0.5:11434/v1")
    assert not is_local_url("https://openrouter.ai/api/v1")


def test_public_view_has_no_key_values():
    view = public_view(load_models())
    dumped = json.dumps(view)
    assert "sk-or-v1" not in dumped
    assert view["endpoints"]["openrouter-opus"]["key_present"] is True


def test_save_rejects_key_inside_file():
    cfg = load_models()
    cfg["endpoints"]["openrouter-opus"]["api_key"] = "sk-123"
    assert validate_models(cfg)
    with pytest.raises(ValueError, match="ключи"):
        save_models(cfg)


def test_hang_is_reported_as_silence(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("silence", request=request)
    cfg = load_models()
    cfg["roles"]["senior"] = ["lmstudio-local"]
    with pytest.raises(RouterError, match="молчит"):
        Router(cfg, transport=httpx.MockTransport(handler)).chat("senior", "s", [{"role": "user", "content": "x"}])


def test_extract_json_tolerates_fences_and_tabs():
    assert extract_json('```json\n{"a": "x\ty"}\n```')["a"] == "x\ty"
    assert extract_json('Вот: {"verdict": "accepted", "risks": []} конец')["verdict"] == "accepted"
