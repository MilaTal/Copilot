"""Доступ к разработке Junior Pilot: только чтение, секреты не читаются и маскируются, проверки кодом находят пробелы."""
import json
import subprocess

import pytest

from app import config, state, workspace


@pytest.fixture
def junior(tmp_path):
    d = tmp_path / "junior"
    (d / "src").mkdir(parents=True)
    (d / ".env").write_text("OPENROUTER_API_KEY=sk-or-v1-REALREALREALREALREALREAL", encoding="utf-8")
    (d / "src" / "agent.py").write_text(
        'API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\n'
        "def run(q):\n    return call_model(q)\n", encoding="utf-8")
    wf = {"nodes": [{"name": "Агент", "type": "@n8n/n8n-nodes-langchain.agent", "parameters": {"options": {}},
                     "onError": "continueRegularOutput"}], "connections": {}}
    (d / "flow.json").write_text(json.dumps(wf, ensure_ascii=False), encoding="utf-8")
    (d / "node_modules").mkdir()
    (d / "node_modules" / "x.js").write_text("max_steps = 1", encoding="utf-8")
    return d


def test_env_and_ignored_dirs_not_read(junior):
    files = workspace.inventory(junior)
    env = next(f for f in files if f["path"] == ".env")
    assert "sha1" not in env and env["skipped"].startswith("секрет")
    assert not any(f["path"].startswith("node_modules") for f in files)


def test_checks_find_secret_missing_limits_and_n8n_problems(junior):
    st = state.create("Разработка", 4)
    st["workspace"] = {"path": str(junior), "cloud_allowed": False}
    rep = workspace.scan(st)
    by = {}
    for f in rep["findings"]:
        by.setdefault(f["check"], []).append(f)
    assert by["secret_in_code"][0]["file"] == "src/agent.py"
    assert by["limits"][0]["level"] == "warning"
    assert by["idempotency"][0]["level"] == "warning"
    assert "n8n_trigger" in by and "n8n_max_iter" in by and "n8n_continue" in by


def test_diff_between_scans(junior):
    st = state.create("Разработка", 4)
    st["workspace"] = {"path": str(junior)}
    workspace.scan(st)
    (junior / "src" / "agent.py").write_text("max_steps = 12\nstatus = 'stopped_by_limit'\n", encoding="utf-8")
    (junior / "src" / "new.py").write_text("x = 1\n", encoding="utf-8")
    rep = workspace.scan(st)
    assert rep["diff"]["changed"] == ["src/agent.py"] and rep["diff"]["added"] == ["src/new.py"]
    by = {f["check"]: f for f in rep["findings"]}
    assert by["limits"]["level"] == "ok" and by["limit_outcome"]["level"] == "ok"


def test_material_for_model_masks_and_skips_env(junior):
    st = state.create("Разработка", 4)
    st["workspace"] = {"path": str(junior), "cloud_allowed": False}
    workspace.scan(st)
    m = workspace.material_for_model(st)
    assert ".env" not in m["files"]
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in m["text"] and "«СКРЫТО»" in m["text"]
    assert m["masked"] >= 1 and m["cloud_allowed"] is False


def test_scan_never_writes_to_junior_folder(junior):
    before = {p: p.stat().st_mtime for p in junior.rglob("*")}
    st = state.create("Разработка", 4)
    st["workspace"] = {"path": str(junior)}
    workspace.scan(st)
    after = {p: p.stat().st_mtime for p in junior.rglob("*")}
    assert before == after


def test_cannot_point_at_copilot_itself():
    with pytest.raises(ValueError, match="самого copilot"):
        workspace.validate_path(str(config.ROOT / "app"))


def test_git_info_read_only(junior):
    subprocess.run(["git", "init", "-q", str(junior)], check=True)
    info = workspace.git_info(junior)
    assert info["is_git"] and isinstance(info["dirty"], list)
