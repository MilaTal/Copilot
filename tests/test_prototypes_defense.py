"""Прототипы из ответов и каркас защиты со сверкой чисел."""
import json
import subprocess
import sys

from app import defense, prototypes, state


def project_full():
    st = state.create("Протоколы совещаний", 1)
    S = state.set_answer
    S(st, 2, "loss_now", "answered", "Протокол — 60 минут секретаря в день; задачи живут 1–2 совещания и теряются")
    S(st, 1, "decision_maker", "answered", "Руководитель, ведущий совещание; число — доля закрытий без ручного ввода")
    S(st, 1, "bottleneck", "answered", "Нет реестра — десятки групп дублей задач")
    S(st, 2, "core_step", "answered", "Сопоставить фразы совещания с задачами реестра")
    S(st, 2, "human_left", "answered", "Тёзки и люди без полного имени")
    # было: отдельный вопрос о числе ЛПР; стало: первая строка KPI — главное число ЛПР
    S(st, 2, "kpi_table", "answered", [["Доля закрытий без ручного ввода", "0 %", "≥ 90 %", "журнал закрытий"],
                                       ["Время до протокола", "60 мин", "≤ 10 мин", "журнал сборки"]])
    S(st, 3, "pattern", "answered", "Конвейер с валидатором")
    S(st, 3, "trigger", "answered", "Файл появился в папке-приёмнике")
    S(st, 3, "tools_table", "answered", [["Папка-приёмник", "чтение", "файлы", "одна папка", "безопасен"],
                                         ["Реестр задач", "запись", "база приложения", "свои таблицы", "с ключом идемпотентности"],
                                         ["Письмо участникам", "запись", "почта", "один ящик", "недопустим без подтверждения"]])
    S(st, 3, "quality_gate", "answered", "Нет цитаты в источнике → статус не меняется")
    S(st, 3, "escalation", "answered", "Карточки администратору проекта до следующей встречи")
    S(st, 4, "limits_table", "answered", [["вызовы", "14", "максимум 10–11", "остановка", "админ"],
                                          ["деньги", "0,40 $", "максимум 0,228", "остановка", "админ"],
                                          ["время", "20 мин", "запас", "остановка", "админ"]])
    S(st, 6, "pilot_scope", "answered", "2 недели, 3 человека, только локальный контур")
    S(st, 5, "runs_table", "answered", [["1", "a", "да", "0.3512"], ["2", "b", "да", "0.1461"], ["3", "c", "нет", "0.2"]])
    S(st, 6, "rollout_table", "answered", [["тень → человек в контуре", "0 больших ошибок за неделю; 15 из 16", "протокол пишет секретарь"]])
    S(st, 6, "risk_table", "answered", [["Назначить исполнителя", "тёзка — 3 отказа из 3", "карточка с подсказками", "администратор проекта"]])
    S(st, 6, "ownership", "answered", "Перед сменой модели — прогон набора, 2 ч, 0 $")
    S(st, 6, "decision_ask", "answered", "Перевести пилот в стадию «человек в контуре» на 4 недели ради 96 % закрытий системой")
    state.save(st, "заполнено")
    return st


def test_diagram_uses_pattern_and_answers():
    st = project_full()
    d = prototypes.agent_diagram(st)
    assert d["pattern"] == "sequential"
    assert "{" + "core}" not in d["mermaid"] and "Сопоставить фразы" in d["mermaid"]
    assert "Реестр задач" in d["mermaid"] and "Папка-приёмник" in d["mermaid"] and d["missing"] == []


def test_n8n_workflow_is_consistent():
    st = project_full()
    wf = prototypes.n8n_workflow(st)
    names = {n["name"] for n in wf["nodes"]}
    for src, kinds in wf["connections"].items():
        assert src in names
        for outs in kinds.values():
            for branch in outs:
                for link in branch:
                    assert link["node"] in names
    agent = next(n for n in wf["nodes"] if n["type"].endswith(".agent"))
    assert agent["parameters"]["options"]["maxIterations"] == 14
    assert agent["onError"] == "stopWorkflow"
    assert any("trigger" in n["type"].lower() or "webhook" in n["type"] for n in wf["nodes"])


def test_generated_n8n_passes_own_workspace_checks():
    from app.workspace import check_n8n
    st = project_full()
    found = {f["check"] for f in check_n8n("p.json", json.dumps(prototypes.n8n_workflow(st), ensure_ascii=False))}
    assert "n8n_trigger" not in found and "n8n_max_iter" not in found and "n8n_continue" not in found


def test_python_skeleton_config_and_its_tests_pass():
    st = project_full()
    files = prototypes.python_skeleton(st)
    folder = state.project_dir(st["id"]) / "prototype" / "python_agent"
    cfg = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    assert cfg["limits"]["max_steps"] == 14 and cfg["limits"]["max_cost_usd"] == 0.4 and cfg["limits"]["max_seconds"] == 1200
    kinds = {t["title"]: t["repeat_class"] for t in cfg["tools"]}
    assert kinds == {"Папка-приёмник": "safe", "Реестр задач": "idempotent", "Письмо участникам": "confirm"}
    assert "agent.py" in files and "tests/test_agent.py" in files
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], cwd=folder,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_defense_slides_reconciled_and_limited():
    st = project_full()
    d = defense.for_project(st)
    assert 3 <= len(d["slides"]) <= 4
    assert all(len(s["lines"]) <= 4 for s in d["slides"])
    assert d["reconcile"] and all(r["match"] for r in d["reconcile"])
    assert "60 минут" in d["slides"][0]["title"]
    cost_line = d["slides"][3]["lines"][0]
    assert "0.3487 $ за успешный результат" in cost_line  # (0,3512 + 0,1461 + 0,2) / 2
    assert d["go"]["verdict"] == "no_go" and any("испытания" in b for b in d["go"]["blockers"]), \
        "без прогона атак взлёт не разрешается"
    assert (state.project_dir(st["id"]) / "docs" / "Каркас защиты.md").exists()
    st["attack_runs"] = [{"ts": state.now(), "target": "x", "summary": {"injection": "passed"}}]
    assert defense.for_project(st)["go"]["verdict"] in ("go", "caution")


def test_defense_marks_gaps_instead_of_inventing():
    st = state.create("Пусто", 1)
    d = defense.for_project(st)
    assert d["go"]["verdict"] == "no_go"
    assert d["gaps"] and all(g.startswith("[ПУСТО") for g in d["gaps"])
    assert not d["reconcile"]


def test_reconcile_flags_number_without_source():
    f = {"loss": [{"text": "60 минут в день", "ref": "Ш1"}]}
    slides = [{"n": 1, "title": "Теряем 75 минут", "lines": []}]
    rows = defense.reconcile(slides, f)
    assert rows == [{"number": "75", "slide": 1, "source": "—", "quote": "", "match": False}]


def test_documents_mode_rejects_quotes_not_in_text(fake_llm):
    from app.router import Router
    text = "Сейчас протокол занимает 60 минут в день. Доля закрытий 96 %. " * 5
    reply = json.dumps({"facts": [{"key": "loss", "quote": "протокол занимает 60 минут в день"},
                                  {"key": "kpi", "quote": "Доля закрытий 99 %"}]}, ensure_ascii=False)
    d = defense.from_documents(text, "Документы", router=Router(transport=fake_llm(reply).transport), only_local=False)
    assert d["rejected_quotes"] == ["Доля закрытий 99 %"]
    assert "60 минут" in d["slides"][0]["title"]
