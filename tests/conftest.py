import json
import shutil
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Каждый тест — своя папка данных и своя копия роутера: рабочие данные и config/models.yaml не трогаются."""
    data = tmp_path / "data"
    models = tmp_path / "models.yaml"
    shutil.copyfile(ROOT / "config" / "models.yaml", models)
    monkeypatch.setenv("COPILOT_DATA_DIR", str(data))
    monkeypatch.setenv("COPILOT_MODELS_FILE", str(models))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-not-real-0000000000000000")
    yield tmp_path


def sse(text: str, usage=(120, 30)) -> bytes:
    chunks = [{"choices": [{"delta": {"content": part}}]} for part in (text[: len(text) // 2], text[len(text) // 2:])]
    chunks.append({"choices": [], "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}})
    body = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return body.encode("utf-8")


class FakeLLMServer:
    """Подмена сервера модели: отвечает заданным текстом, умеет падать на выбранных хостах."""

    def __init__(self, reply="ok", fail_hosts=()):
        self.reply = reply
        self.fail_hosts = set(fail_hosts)
        self.calls = []

    def __call__(self, request: httpx.Request):
        self.calls.append(request)
        if request.url.host in self.fail_hosts:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, content=sse(self.reply), headers={"content-type": "text/event-stream"})

    @property
    def transport(self):
        return httpx.MockTransport(self)


@pytest.fixture
def fake_llm():
    return FakeLLMServer
