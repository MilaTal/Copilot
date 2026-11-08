"""Проверки каркаса без настоящей модели: исходы прогона различимы, потолки срабатывают до вызова."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from agent import Agent  # noqa: E402
from llm import Reply  # noqa: E402
from store import Store  # noqa: E402


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, system, messages):
        self.calls += 1
        text = self.replies.pop(0) if self.replies else '{"action": "tool", "tool": "example_read", "args": {"query": "x"}}'
        return Reply(text, "fake", 100, 20, 0.01)


def make(tmp_path, replies, **limits):
    cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8")) if (HERE / "config.json").exists() else {
        "core_step": "тест", "success_criterion": "тест", "tools": [], "llm": {"endpoints": []}}
    cfg["limits"] = {"max_steps": 3, "max_cost_usd": 1.0, "max_seconds": 60, "max_artifacts": 5,
                     "max_input_chars": 1000, **limits}
    cfg["tools"] = [{"name": "example_read", "title": "Чтение", "access": "чтение", "system": "тест",
                     "rights": "чтение", "repeat_class": "safe"}]
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    llm = FakeLLM(replies)
    return Agent(p, llm=llm, store=Store(tmp_path / "a.db")), llm


def test_done(tmp_path):
    a, _ = make(tmp_path, ['{"action": "final", "answer": "готово"}'])
    r = a.run("задача")
    assert r.status == "done" and r.answer == "готово"


def test_stopped_by_limit_differs_from_done(tmp_path):
    a, llm = make(tmp_path, [], max_steps=3)
    r = a.run("крутись бесконечно")
    assert r.status == "stopped_by_limit"
    assert "3 из 3" in r.reason
    assert llm.calls == 3
    assert a.store.open_queue(), "остановка по пределу уходит в ручную очередь"


def test_big_input_rejected_before_model(tmp_path):
    a, llm = make(tmp_path, [], max_input_chars=10)
    r = a.run("x" * 100)
    assert r.status == "stopped_by_limit" and llm.calls == 0


def test_empty_input_rejected(tmp_path):
    a, llm = make(tmp_path, [])
    assert a.run("").status == "rejected" and llm.calls == 0


def test_repeat_after_limit_does_not_spend_again(tmp_path):
    a, llm = make(tmp_path, [], max_steps=3)
    r1 = a.run("крутись", request_key="k-limit")
    r2 = a.run("крутись", request_key="k-limit")
    assert r1.status == "stopped_by_limit" and r2.status == "stopped_by_limit"
    assert r2.reason.startswith("already_done") and llm.calls == 3, "повтор после предела снова тратил вызовы"


def test_same_request_key_not_repeated(tmp_path):
    a, llm = make(tmp_path, ['{"action": "final", "answer": "закрыто"}'])
    a.run("закрой задачу", request_key="k1")
    r2 = a.run("закрой задачу", request_key="k1")
    assert r2.reason.startswith("already_done") and llm.calls == 1
