"""Ядро: 6 шагов Ш1–Ш6 по два отрезка, у каждого вопроса есть зачем, подсказка, пример и риск; ни слова об учёбе;
примеры обезличены; таблица переезда со старого маршрута полная."""
import re
from pathlib import Path

from app import core

ROOT = Path(__file__).resolve().parent.parent
SKILLS = Path.home() / ".claude" / "skills"


def test_six_steps_in_order():
    """Было: 12 шагов с прежними кодами. Стало: 6 шагов Ш1–Ш6, три фазы по два шага, у шага два отрезка и ворота."""
    ids = [s["id"] for s in core.steps()]
    assert ids == list(range(1, 7)) and core.step_count() == 6
    assert [s["code"] for s in core.steps()] == [f"Ш{i}" for i in range(1, 7)]
    assert [s["slug"] for s in core.steps()] == ["problem", "solution", "architecture", "prototype", "trials", "launch"]
    covered = sorted(x for p in core.phases() for x in p["steps"])
    assert covered == ids and all(len(p["steps"]) == 2 for p in core.phases())
    for s in core.steps():
        segs = s["segments"]
        assert len(segs) == 2 and all(g["title"] and g["questions"] for g in segs), s["code"]
        assert [x for g in segs for x in g["questions"]] == [q["id"] for q in s["questions"]], s["code"]
        assert s["gate"]["n"] == s["id"] and s["gate"]["checks"], s["code"]
    assert sum(len(s["questions"]) for s in core.steps()) == 68
    all_ids = [q["id"] for s in core.steps() for q in s["questions"]]
    assert len(all_ids) == len(set(all_ids)), "id вопроса уникален на весь маршрут — по нему код находит шаг"


def test_core_texts_have_no_old_codes():
    """В текстах ядра (значениях, не в комментариях) нет кодов прежнего маршрута вида WP01."""
    import yaml
    rx = re.compile(r"WP\d")
    bad = []

    def walk(x, where):
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, f"{where}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{where}[{i}]")
        elif isinstance(x, str) and rx.search(x):
            bad.append(where)
    for f in (ROOT / "core").glob("*.yaml"):
        walk(yaml.safe_load(f.read_text(encoding="utf-8")), f.name)
    assert not bad, bad[:10]


def test_migration_table_covers_all_77_old_questions():
    mig = core.migration()
    assert mig["schema"] == 3 and len(mig["map"]) == 77
    for old, new in mig["map"].items():
        ns, nq = new.split(":")
        assert core.qstep(nq) == int(ns), (old, new)
    assert set(mig["merge_rules"]) <= set(mig["map"])
    assert sorted(set(int(v) for v in mig["step_map"].values())) == core.step_ids()
    for x in mig["new_questions"]:
        s, q = x.split(":")
        assert core.qstep(q) == int(s)


def test_knowledge_base_six_steps_without_sources():
    """База знаний наставника: советы на каждый шаг, у каждого совет и вопрос; в текстах нет имён источников.
    Пометка источника живёт только в поле source и только из разрешённого списка."""
    items = core.knowledge()
    assert {a["step"] for a in items} == set(core.step_ids())
    src = re.compile(r"(?i)триз|triz|альтшуллер|anthropic|openai|google|agent-practice")
    allowed = {"ТРИЗ", "теория управления проектами", "AI-native playbook", "исследование рынка аналогичных проектов и практика"}
    for a in items:
        assert a["advice"].strip() and a["question"].strip() and a["when"].strip(), a["id"]
        text = a["advice"] + a["question"] + a["when"]
        assert not src.search(text), a["id"]  # пометка источника — только в поле source, не в тексте
        assert "playbook" not in text.lower(), a["id"]
        parts = [p.strip() for p in str(a.get("source") or "").split(";") if p.strip()]
        assert set(parts) <= allowed, a["id"]


def test_every_question_is_complete():
    for s in core.steps():
        assert s["ready"] and s["goal"] and s["artifact"]
        qids = [q["id"] for q in s["questions"]]
        assert len(qids) == len(set(qids)), s["code"]
        for q in s["questions"]:
            for k in ("text", "why", "hint"):
                assert q[k].strip(), (s["code"], q["id"], k)
            # у вопроса-решения (no_sample) образца нет по замыслу — только варианты с плюсами и минусами
            assert q.get("no_sample") or q.get("example", "").strip(), (s["code"], q["id"], "example")
            assert q["risk"]["level"] in ("warning", "caution")
            if q["type"] == "table":
                assert q["columns"] and q.get("min_rows", 1) >= 1
            if q["type"] in ("choice", "multi"):
                assert len(q["options"]) >= 2


def _texts():
    files = list((ROOT / "core").glob("*.yaml")) + list((ROOT / "static").glob("*.*")) + list((ROOT / "app").glob("*.py"))
    files += list((ROOT / "templates").rglob("*.py")) + list((ROOT / "templates").rglob("*.md"))
    for name in ("ai-automation-copilot", "ai-project-review"):
        f = SKILLS / name / "SKILL.md"
        if f.exists():
            files.append(f)
    return files




def test_examples_have_no_personal_names():
    """Примеры обезличены: нет конструкций «Фамилия И.» / «И. Фамилия» и почтовых адресов, кроме example.com."""
    name_rx = re.compile(r"\b[А-ЯЁ][а-яё]{2,}\s[А-ЯЁ]\.(\s?[А-ЯЁ]\.)?|\b[А-ЯЁ]\.\s?[А-ЯЁ][а-яё]{2,}")
    mail_rx = re.compile(r"[\w.-]+@(?!example\.com)[\w-]+\.\w+")
    for f in (ROOT / "core").glob("*.yaml"):
        text = f.read_text(encoding="utf-8")
        assert not name_rx.search(text), (f.name, name_rx.search(text).group())
        assert not mail_rx.search(text), (f.name, mail_rx.search(text).group())


def test_attack_choices_match_trials_options():
    trials = core.sid("trials")
    opts = set(core.question(trials, "attack_selection")["options"])
    assert {a["choice"] for a in core.attacks()} == opts
    assert {a["step"] for a in core.attacks()} == {trials}


def test_pattern_choice_map_covers_options():
    opts = core.question(3, "pattern")["options"]
    cmap = core._load("patterns.yaml")["choice_map"]
    assert set(opts) == set(cmap)
    ids = {p["id"] for p in core.patterns()}
    assert set(cmap.values()) <= ids
