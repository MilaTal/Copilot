"""Противоречия между шагами: каждое правило ловит своё расхождение и молчит, когда ответы согласованы."""
from app import consistency, defense, readiness, state


def ids(st):
    return {c["id"] for c in consistency.check(st)}


def test_kpi_before_mismatch():
    """Было: «до» в KPI сверялось с отдельным числом ЛПР. Стало: число ЛПР — первая строка KPI, и у неё должны быть
    «до», цель и замер (ворота Ш2)."""
    st = state.create("KPI", 1)
    state.set_answer(st, 2, "kpi_table", "answered", [["Доля закрытий задач без ручного ввода", "", "90 %", "журнал"]])
    found = [c for c in consistency.check(st) if c["id"] == "c:kpi_before"]
    assert found and "значения «до»" in found[0]["text"] and found[0]["step"] == 2
    state.set_answer(st, 2, "kpi_table", "answered", [["Доля закрытий задач без ручного ввода", "0 %", "90 %", "журнал"]])
    assert "c:kpi_before" not in ids(st)


def test_perimeter_local_but_cloud_model():
    st = state.create("Периметр", 4)
    state.set_answer(st, 2, "secret_kind", "answered", "Тайна в процессе — всё локально")
    state.set_answer(st, 3, "model_per_step", "answered", [["Генерация", "топовая через OpenRouter API", "качество"]])
    found = [c for c in consistency.check(st) if c["id"] == "c:perimeter"]
    assert found and found[0]["level"] == "warning" and "OpenRouter" in found[0]["text"]


def test_write_action_without_write_tool():
    st = state.create("Запись", 2)
    state.set_answer(st, 2, "write_action", "answered", "Закрывает задачу в реестре")
    state.set_answer(st, 3, "tools_table", "answered", [["Папка", "чтение", "файлы", "одна папка", "безопасен"]])
    assert "c:write_tool" in ids(st)
    state.set_answer(st, 3, "tools_table", "answered", [["Реестр", "запись", "база", "свои таблицы", "с ключом"]])
    assert "c:write_tool" not in ids(st)


def test_core_step_missing_in_to_be():
    st = state.create("Ядро", 2)
    state.set_answer(st, 2, "core_step", "answered", "Сопоставить фразы совещания с задачами реестра")
    state.set_answer(st, 2, "to_be_table", "answered", [["Принять файл", "workflow", "нет", "—"]])
    assert "c:core_in_tobe" in ids(st)
    state.set_answer(st, 2, "to_be_table", "answered", [["Сопоставить фразы с задачами", "агент", "экран", "карточка"]])
    assert "c:core_in_tobe" not in ids(st)


def test_run_over_cost_cap_and_manual_trigger():
    st = state.create("Потолок", 4)
    state.set_answer(st, 4, "limits_table", "answered", [["деньги", "0,40 $", "", "", ""]])
    state.set_answer(st, 5, "runs_table", "answered", [["1", "a", "да", "0.35"], ["2", "b", "да", "0.62"]])
    state.set_answer(st, 3, "trigger", "answered", "Захожу утром и запускаю кнопкой")
    assert {"c:run_over_cap", "c:manual_trigger"} <= ids(st)


def test_agency_vs_pattern_and_blind_approval():
    st = state.create("Согласованность", 2)
    state.set_answer(st, 2, "agency_level", "answered", "Workflow — шаги заданы заранее")
    state.set_answer(st, 3, "pattern", "answered", "Supervisor + субагенты")
    state.set_answer(st, 3, "control_table", "answered", [["Оплата", "необратимый", "утвердить", "бухгалтер", "деньги"]])
    # было: отдельный выбор «видит ли человек исполняемое действие» = «Нет»; стало: ответ о точке остановки
    state.set_answer(st, 3, "stop_point", "answered", "Экран согласования — показываем описание, а не параметры")
    assert {"c:agency_vs_pattern", "c:approval_blind"} <= ids(st)
    state.set_answer(st, 3, "stop_point", "answered", "Экран оплаты: сумма и получатель из того же поля, что уйдёт в вызов")
    assert "c:approval_blind" not in ids(st)


def test_consistent_project_has_no_findings():
    st = state.create("Согласованный", 1)
    state.set_answer(st, 2, "secret_kind", "answered", "Тайна в процессе — всё локально")
    state.set_answer(st, 3, "model_per_step", "answered", [["Генерация", "локальная 27B", "периметр"]])
    state.set_answer(st, 3, "trigger", "answered", "Файл появился в папке-приёмнике")
    assert ids(st) == set()


def test_findings_are_risks_and_block_takeoff():
    st = state.create("Блокер", 4)
    state.set_answer(st, 2, "secret_kind", "answered", "Тайна в процессе — всё локально")
    state.set_answer(st, 4, "hosting_table", "answered", [["Генерация", "API", "расшифровка", "—", "3 $"]])
    r = next(r for r in readiness.risks(st) if r["id"] == "c:perimeter")
    assert r["reason"] == "Расхождение между шагами" and r["level"] == "warning"
    st["attack_runs"] = [{"ts": state.now(), "target": "x", "summary": {"injection": "passed"}}]
    d = defense.for_project(st)
    assert d["go"]["verdict"] == "no_go" and any(b.startswith("Ш2") for b in d["go"]["blockers"])
