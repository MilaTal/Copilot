"""Переезд сохранённых проектов с маршрута из 12 шагов (схема 2) на 6 шагов (схема 3): без потерь, идемпотентно,
с резервной копией файла рядом. Таблица переезда — из ядра (core/steps.yaml, migration)."""
import copy
import json
import shutil
from pathlib import Path

import pytest

from app import config, core, migration, readiness, recommend, state

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "data" / "projects" / "demo-protokol-soveschaniya-iz-zapisi-3a26"

# Форма прежних слитых вопросов (их больше нет в ядре): тип и, для таблиц, число колонок.
OLD_SHAPE = {"2:origin": "text", "3:human_refusal": "text", "3:constraints": "text", "4:business_kpi": "text",
             "6:confirm_what_runs": "choice", "7:manual_queue": "text", "7:bad_input": "text", "7:partial_failure": "text",
             "8:cost_limit_place": "text", "4:fate_table": 3, "11:excessive_agency": 4}


def _rec(value, note=""):
    return {"status": "answered", "value": value, "note": note, "updated": "2026-10-09T12:00:00", "senior": None}


def _full_schema2() -> tuple[dict, dict]:
    """Проект схемы 2, где отвечены все 77 прежних вопросов. В каждом ответе — своя метка «M-шаг-id»."""
    mig = core.migration()
    answers: dict = {}
    marks = {}
    for old, new in mig["map"].items():
        os_, oq = old.split(":")
        ns, nq = new.split(":")
        m = f"M-{os_}-{oq}"
        marks[old] = m
        shape = OLD_SHAPE.get(old)
        q = core.question(int(ns), nq)
        if old == "3:boundary_table":  # прежние колонки: шаг, что происходит, вердикт, почему
            rec = _rec([["Свести протокол", f"решение {m}", "агент", "по смыслу"]])
        elif old == "4:fate_table":
            rec = _rec([["Свести протокол", "перепроектирован", f"признак {m}"], ["Загрузить файлы", "схлопнут с 1", "та же папка"]])
        elif old == "11:excessive_agency":
            rec = _rec([["Реестр задач", f"удаление {m}", "вся база", "закрывает без цитаты"]])
        elif shape == "choice":
            rec = _rec("Частично — показываем описание, а не параметры", note=m)
        elif shape == "text":
            rec = _rec(f"Ответ {m}")
        elif q["type"] == "table":
            n = len(q["columns"]) - (1 if nq in ("tools_table", "limits_table") else 0)  # прежняя ширина
            first = "деньги" if nq == "limits_table" else ("Реестр задач" if nq == "tools_table" else f"строка {m}")
            rec = _rec([[first] + [f"{m} {i}" for i in range(1, n)]] + [[f"ещё {i}"] + [""] * (n - 1) for i in range(3)])
        elif q["type"] == "choice":
            rec = _rec(q["options"][0], note=m)
        elif q["type"] == "multi":
            opts = list(q["options"])
            if nq == "delivery_package":  # прежний вариант с номерами шагов 1–12
                opts = [o.replace("Ш1–Ш6", "1–12") for o in opts]
            rec = _rec(opts[:3], note=m)
        else:
            rec = _rec(f"Ответ {m}")
        answers.setdefault(os_, {})[oq] = rec
    st = {"id": "full-schema2-0001", "name": "Полный проект старой схемы", "description": "", "domain": "",
          "created": "2026-10-01T10:00:00", "updated": "2026-10-09T12:00:00", "start_step": 3, "current_step": 11,
          "answers": answers, "artifacts": {"4": {"built": "2026-10-05T10:00:00", "path": "WP04.md", "stale": False},
                                            "12": {"built": "2026-10-06T10:00:00", "path": "WP12.md", "stale": False}},
          "risks_extra": [{"id": "xabc", "step": 11, "text": "Испытание не пройдено", "level": "warning", "source": "attack",
                           "owner": "", "status": "open", "created": "x"},
                          {"id": "xdef", "step": 4, "text": "Совет", "level": "caution", "source": "senior", "owner": "",
                           "status": "open", "created": "x", "key": "4.business_kpi"}],
          "risk_owners": {"q:4:business_kpi": "финансы", "q:4:kpi_table": "руководитель", "q:9:pilot_scope": "ИБ",
                          "run:11": "инженер"},
          "mentor": {"3:constraints": {"guided": 2}, "9:secret_kind": {"guided": 1}, "4:business_kpi": {"guided": 1},
                     "4:kpi_table": {"guided": 1}},
          "synthesis": {"1": {"text": "Вывод идеи", "confirmed": "2026-10-02T10:00:00"},
                        "2": {"text": "Вывод As-Is", "confirmed": "2026-10-03T10:00:00"}},
          "recs": {recommend._rid("q:4:business_kpi"): {"status": "deferred", "defer_step": 9, "created": "x", "seen": True}},
          "target": {}, "attack_runs": [], "eval_runs": [], "history": [], "schema": 2}
    return st, marks


def _dump(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def test_every_old_answer_lands_somewhere():
    st, marks = _full_schema2()
    assert len(marks) == 77
    migration.migrate(st)
    rules = core.migration()["merge_rules"]
    for old, m in marks.items():
        ns, nq = core.migration()["map"][old].split(":")
        rec = st["answers"][ns][nq]
        if old in rules:
            assert any(x["from"] == old for x in rec.get("merged_from", [])), old
        assert m in _dump(rec), (old, "метка не дошла до ответа-приёмника")
    assert st["schema"] == 3 and not st["migrations"][-1]["unplaced"]


def test_merge_rules_shape_answers():
    st, _ = _full_schema2()
    migration.migrate(st)
    a = st["answers"]
    assert a["1"]["as_is_table"]["value"][0][0].startswith("Ответ M-2-origin (из прежнего шага 2)")
    kpi = a["2"]["kpi_table"]["value"]
    assert kpi[0][0].startswith("Ответ M-4-business_kpi") and len(kpi[0]) == 4
    assert "M-3-human_refusal" in a["2"]["human_left"]["value"] and "(из прежнего шага 3)" in a["2"]["human_left"]["value"]
    sk = a["2"]["secret_kind"]
    assert sk["value"] in core.question(2, "secret_kind")["options"] and "M-3-constraints" in sk["extra"]
    assert "описание, а не параметры" in a["3"]["stop_point"]["value"]
    assert "M-7-manual_queue" in a["3"]["escalation"]["value"]
    fail = a["3"]["failures_table"]["value"]
    q = core.question(3, "failures_table")
    assert [r[0] for r in fail[:2]] == q["required_rows"] and state.missing_required_rows(q, fail) == []
    bt = a["2"]["boundary_table"]["value"]
    row = next(r for r in bt if r[0] == "Свести протокол")
    assert row[1] == "перепроектирован" and row[2] == "агент" and "что происходит" in row[3] and "M-4-fate_table" in row[3]
    assert any(r[0] == "Загрузить файлы" for r in bt)  # строка судьбы без пары — добавлена
    tools = a["3"]["tools_table"]["value"]
    assert all(len(r) == 6 for r in tools) and "M-11-excessive_agency" in tools[0][5]
    lim = a["4"]["limits_table"]["value"]
    assert all(len(r) == 6 for r in lim) and "M-8-cost_limit_place" in lim[0][5]
    assert "Паспорт проекта (документы шагов Ш1–Ш6)" in a["6"]["delivery_package"]["value"]
    assert "pilot_scope" in a["6"] and "secret_kind" not in a.get("4", {})


def test_state_around_answers_moves_too():
    st, _ = _full_schema2()
    migration.migrate(st)
    assert st["current_step"] == 5 and st["start_step"] == 2
    assert st["risk_owners"]["q:2:kpi_table"] in ("финансы", "руководитель")
    assert st["risk_owners"]["q:6:pilot_scope"] == "ИБ" and st["risk_owners"]["run:attacks"] == "инженер"
    assert st["mentor"]["2:secret_kind"]["guided"] == 3 and st["mentor"]["2:kpi_table"]["guided"] == 2
    assert {r["id"]: r["step"] for r in st["risks_extra"]} == {"xabc": 5, "xdef": 2}
    assert st["risks_extra"][1]["key"] == "2.kpi_table"
    syn = st["synthesis"]["1"]
    assert syn["needs_reconfirm"] and "Вывод идеи" in syn["text"] and "Вывод As-Is" in syn["text"]
    assert set(st["artifacts"]) == {"2", "6"} and all(x["stale"] for x in st["artifacts"].values())
    rid = recommend._rid("q:2:kpi_table")
    assert st["recs"][rid]["status"] == "deferred" and st["recs"][rid]["defer_step"] == 4
    readiness.risks(st)  # новое состояние читается без ошибок
    readiness.annunciator(st)


def test_second_migration_changes_nothing():
    st, _ = _full_schema2()
    migration.migrate(st)
    once = copy.deepcopy(st)
    assert migration.migrate(st) is False
    assert st == once


def test_load_makes_backup_once_and_keeps_file(tmp_path):
    st, marks = _full_schema2()
    d = config.projects_dir() / st["id"]
    (d / "versions").mkdir(parents=True)
    raw = json.dumps(st, ensure_ascii=False, indent=1)
    (d / "state.json").write_text(raw, encoding="utf-8")
    back = state.load(st["id"])
    assert back["schema"] == 3
    backups = list(d.glob("backup-schema2-*.json"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == raw
    state.load(st["id"])
    assert len(list(d.glob("backup-schema2-*.json"))) == 1
    assert (d / "state.json").read_text(encoding="utf-8") == raw  # загрузка сама файл не перезаписывает
    state.save(back, "после переезда")
    again = state.load(st["id"])
    assert again["schema"] == 3 and again["answers"] == back["answers"]


@pytest.mark.skipif(not (DEMO / "state.json").exists(), reason="нет демо-проекта в data/projects")
def test_real_demo_project_migrates_without_loss():
    """Реальный сохранённый демо-проект: копия во временную папку, оригинал не трогается."""
    dst = config.projects_dir() / DEMO.name
    shutil.copytree(DEMO, dst)
    before = json.loads((DEMO / "state.json").read_text(encoding="utf-8"))
    orig_bytes = (DEMO / "state.json").read_bytes()
    st = state.load(DEMO.name)
    assert st["schema"] == 3
    n_before = sum(len(v) for v in before["answers"].values())
    n_after = sum(len(v) for v in st["answers"].values())
    assert n_after == n_before
    for s, recs in before["answers"].items():
        for qid, rec in recs.items():
            new = core.migration()["map"][f"{s}:{qid}"]
            ns, nq = new.split(":")
            assert rec["value"] in _dump(st["answers"][ns][nq]) or str(rec["value"]) in _dump(st["answers"][ns][nq])
    assert set(st["recs"]) == set(before.get("recs") or {})
    assert core.has_step(st["current_step"]) and list(dst.glob("backup-schema2-*.json"))
    assert (DEMO / "state.json").read_bytes() == orig_bytes  # оригинал не тронут
    readiness.all_status(st)
