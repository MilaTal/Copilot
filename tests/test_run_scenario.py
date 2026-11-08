"""Прогон по сценарию без сети: флаги, отказ ветки атак без двух подтверждений и без стенда, --dry-run с --keep
на 6 шагах со всеми ответами без архива, ветка атак против подменённого стенда механизмом приложения."""
import json

import httpx
import pytest

from app import attacks, config, core, state
from scenarios import run_scenario as rs


@pytest.fixture
def no_model(monkeypatch):
    """Любая попытка создать роутер модели — провал теста: --dry-run обязан обходиться без модели."""
    import app.router

    def boom(*a, **k):
        raise AssertionError("в --dry-run вызвана модель")
    monkeypatch.setattr(app.router, "Router", boom)


@pytest.fixture
def no_attacks(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("атаки запущены без разрешения")
    monkeypatch.setattr(attacks, "execute", boom)
    monkeypatch.setattr(attacks, "arm", boom)


def _archive():
    return config.data_dir() / "_archive_test_projects"


# ---------- флаги ----------
def test_parse_flags_defaults():
    a = rs.parse_args([])
    assert a.scenario == "demo_scenario"
    assert not (a.dry_run or a.keep or a.attacks or a.confirm1) and a.confirm2 is None


def test_parse_flags_all():
    a = rs.parse_args(["demo_scenario", "--dry-run", "--keep", "--attacks", "--confirm-attacks-1",
                       "--confirm-attacks-2", "127.0.0.1:8100"])
    assert a.dry_run and a.keep and a.attacks and a.confirm1 and a.confirm2 == "127.0.0.1:8100"
    assert rs.attack_gate(a) is None
    assert rs.parse_args(["--confirm-attacks-2"]).confirm2 is True


@pytest.mark.parametrize("argv", [
    ["--attacks"],
    ["--attacks", "--confirm-attacks-1"],
    ["--attacks", "--confirm-attacks-2", "127.0.0.1:8100"],
])
def test_gate_needs_two_separate_confirmations(argv):
    msg = rs.attack_gate(rs.parse_args(argv))
    assert msg and "нужны два раздельных подтверждения пользователя" in msg


def test_gate_second_confirmation_is_typed_host():
    msg = rs.attack_gate(rs.parse_args(["--attacks", "--confirm-attacks-1", "--confirm-attacks-2"]))
    assert msg and "хост стенда" in msg


def test_no_attacks_flag_no_gate():
    assert rs.attack_gate(rs.parse_args(["--confirm-attacks-1"])) is None


def test_expect_normalized():
    assert rs.norm_expect("detail") == rs.norm_expect("needs_detail") == "needs_detail"
    assert rs.norm_expect("accept") == rs.norm_expect("accepted") == "accepted"


def test_old_scenario_still_translated():
    mig = core.migration()
    merged = next(iter(mig["merge_rules"]))
    moved = next(k for k in mig["map"] if k not in mig["merge_rules"])
    sc = {"project": {"name": "x"},
          "answers": [{"step": k.split(":")[0], "qid": k.split(":")[1], "value": "x"} for k in (moved, merged)]}
    out, skipped = rs.to_route(sc)
    assert len(out) == 1 and len(skipped) == 1
    assert all(core.question(a["step"], a["qid"]) for a in out)
    native, none = rs.to_route(rs.load_scenario("demo_scenario"))
    assert len(native) == 68 and none == []


# ---------- --dry-run ----------
def test_dry_run_keep_creates_project_on_six_steps(no_model, no_attacks):
    summary = rs.run(rs.parse_args(["demo_scenario", "--dry-run", "--keep"]))
    assert summary["ok"], summary["problems"]
    assert summary["steps"] == 6 and summary["answers"] == summary["questions"] == 68
    assert summary["docs"] == 6
    ids = [p["id"] for p in state.list_projects()]
    assert summary["project"] in ids
    st = state.load(summary["project"])
    assert st["name"] == "Вымышленный пример: сверка счетов с заказами"
    assert sorted(int(k) for k in st["answers"]) == core.step_ids()
    assert sum(len(v) for v in st["answers"].values()) == 68
    assert all(r["senior"] is None for v in st["answers"].values() for r in v.values())
    assert state.get_answer(st, core.qstep("secret_kind"), "secret_kind").get("extra")
    assert not _archive().exists()


def test_dry_run_without_keep_goes_to_archive(no_model, no_attacks, capsys):
    assert rs.main(["demo_scenario", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] and out["project"] not in [p["id"] for p in state.list_projects()]
    assert list(_archive().glob(f"*/{out['project']}/state.json"))


def test_attacks_without_two_confirmations_main_run_still_goes(no_model, no_attacks, capsys):
    code = rs.main(["demo_scenario", "--dry-run", "--keep", "--attacks", "--confirm-attacks-1"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 2
    assert out["ok"] and out["answers"] == 68
    assert out["attacks"]["ran"] is False and "два раздельных подтверждения" in out["attacks"]["refusal"]
    assert not state.load(out["project"]).get("attack_runs")


# ---------- стенд ----------
def _project_with(answers: dict) -> dict:
    st = state.create("Стенд", 1)
    for qid, v in answers.items():
        state.set_answer(st, core.qstep(qid), qid, "answered", v)
    state.save(st, "тест")
    return st


def test_attacks_without_stand_refused(no_attacks):
    st = _project_with({"attack_selection": ["Разгон стоимости"],
                        "attack_target": "Стенд на отдельной машине с копией базы"})
    res = rs.run_attacks(st["id"], {"project": {"name": "x"}, "answers": []}, "127.0.0.1:8100")
    assert res["ran"] is False and "стенд не задан" in res["refusal"]
    assert not state.load(st["id"]).get("attack_runs")


def test_attacks_against_prod_like_target_refused(no_attacks):
    st = _project_with({"endpoint": "POST http://10.0.0.5:8100/run", "attack_selection": ["Разгон стоимости"],
                        "attack_target": "На рабочей базе документооборота"})
    res = rs.run_attacks(st["id"], {"project": {"name": "x"}, "answers": []}, "10.0.0.5:8100")
    assert res["ran"] is False and "рабочую систему" in res["refusal"]


def test_attacks_host_not_named_as_stand_refused(no_attacks):
    st = _project_with({"endpoint": "POST http://10.0.0.5:8100/run", "attack_selection": ["Разгон стоимости"],
                        "attack_target": "Стенд 10.0.0.9 с копией базы"})
    res = rs.run_attacks(st["id"], {"project": {"name": "x"}, "answers": []}, "10.0.0.5:8100")
    assert res["ran"] is False and "не назван стендом" in res["refusal"]


def test_stand_from_endpoint_answer():
    st = _project_with({"endpoint": "POST http://127.0.0.1:8100/run — стенд, поле входа query"})
    t = rs.stand_target(st, {})
    assert t["url"] == "http://127.0.0.1:8100/run" and t["method"] == "POST"


def test_attacks_with_both_confirmations_hit_only_stand_and_record(no_model):
    calls = []

    def stand(request: httpx.Request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"answer": "отказ: вне задачи", "status": "rejected", "cost_usd": 0.0})

    summary = rs.run(rs.parse_args(["demo_scenario", "--dry-run", "--keep", "--attacks", "--confirm-attacks-1",
                                    "--confirm-attacks-2", "127.0.0.1:8100"]), transport=httpx.MockTransport(stand))
    res = summary["attacks"]
    assert res["ran"] is True, res
    assert calls and all(u == "http://127.0.0.1:8100/run" for u in calls)
    st = state.load(summary["project"])
    assert st["attack_runs"] and st["target"]["url"] == "http://127.0.0.1:8100/run"
    assert "attack_arm" not in st  # ключ пуска одноразовый
    assert (state.project_dir(st["id"]) / "docs" / res["protocol"]).exists()


def test_attacks_wrong_typed_host_refused(no_model):
    def stand(request):
        raise AssertionError("запрос ушёл при неверном хосте")
    summary = rs.run(rs.parse_args(["demo_scenario", "--dry-run", "--keep", "--attacks", "--confirm-attacks-1",
                                    "--confirm-attacks-2", "127.0.0.1:9999"]), transport=httpx.MockTransport(stand))
    assert summary["attacks"]["ran"] is False and "не совпадает" in summary["attacks"]["refusal"]
    assert not state.load(summary["project"]).get("attack_runs")
