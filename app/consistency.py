"""Противоречия между шагами — проверка кодом, без модели.

Каждый ответ по отдельности может быть хорошим, а вместе они расходятся: у главного числа ЛПР нет «до»;
тайна «всё локально», а модель на шаге облачная. Расхождение показывается риском с цитатами обеих сторон
и не примиряется: какая сторона верна — решает человек. Именно эти вопросы зададут на защите.
Шаг вопроса и его код берутся из ядра (core.qstep), номера шагов в коде не держатся."""
from __future__ import annotations

import re

from . import core
from .state import get_answer

NUM = r"(\d+(?:[.,]\d+)?)"
# человек подтверждает не то, что исполнится: прежние варианты «Нет» и «Частично — показываем описание, а не параметры»
BLIND = re.compile(r"(?i)описани\w*,?\s+а\s+не\s+параметр|не\s+видит|не\s+показыва|:\s*нет\b|только\s+описани")


def _v(st, qid):
    rec = get_answer(st, core.qstep(qid), qid)
    if not rec or rec["status"] not in ("answered", "needs_detail"):
        return None
    return rec["value"]


def _c(qid) -> str:
    return core.code(core.qstep(qid))


def _q(text, n=90) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return f"«{t[:n - 1]}…»" if len(t) > n else f"«{t}»"


def _num(s) -> float | None:
    m = re.search(NUM, str(s or "").replace(" ", ""))
    return float(m.group(1).replace(",", ".")) if m else None


def _words(s) -> set[str]:
    return {w for w in re.findall(r"[а-яёa-z]{4,}", str(s or "").lower())}


def check(st: dict) -> list[dict]:
    out = []

    def add(rule, qid, level, text):
        out.append({"id": f"c:{rule}", "step": core.qstep(qid), "level": level, "text": text})

    # 2. Главное число ЛПР (первая строка KPI) без «до», цели или замера
    kpis = _v(st, "kpi_table") or []
    if kpis:
        r = (list(kpis[0]) + ["", "", "", ""])[:4]
        miss = [name for name, x in (("значения «до»", r[1]), ("цели", r[2]), ("способа замера", r[3])) if not str(x).strip()]
        if miss:
            add("kpi_before", "kpi_table", "caution",
                f"Главное число ЛПР {_q(r[0], 60)} в {_c('kpi_table')} без " + ", ".join(miss)
                + " — без них число не проверить на защите.")

    # 3. Периметр: тайна «всё локально», а модели или размещение — облачные
    secret = str(_v(st, "secret_kind") or "")
    cloud_rx = re.compile(r"(?i)\b(api|облак\w*|openrouter|gpt|claude|gemini|yandexgpt|gigachat)\b")
    if secret.startswith("Тайна в процессе"):
        hits = [f"{_c('model_per_step')}: {_q(' · '.join(r), 70)}" for r in _v(st, "model_per_step") or []
                if cloud_rx.search(" ".join(r))]
        hits += [f"{_c('hosting_table')}: {_q(' · '.join(r), 70)}" for r in _v(st, "hosting_table") or []
                 if len(r) > 1 and cloud_rx.search(r[1])]
        if hits:
            add("perimeter", "secret_kind", "warning",
                f"В {_c('secret_kind')} выбрано «тайна в процессе — всё локально», но облачная модель или размещение есть в "
                + "; ".join(hits[:3]) + ". Наружу уйдёт то, что по решению уходить не должно.")
    if secret.startswith("Тайна в данных"):
        rows = _v(st, "hosting_table") or []
        if rows and not any("гибрид" in (r[1] if len(r) > 1 else "").lower() for r in rows) and \
                any(cloud_rx.search(r[1] if len(r) > 1 else "") for r in rows):
            add("perimeter_filter", "hosting_table", "caution",
                f"В {_c('secret_kind')} «тайна в данных — гибрид с фильтром», но в таблице размещения {_c('hosting_table')} "
                "нет ни одного гибридного шага, а API есть. Где стоит фильтр персональных данных перед внешней моделью?")

    # 4. Действие во внешней системе заявлено, а инструмента записи нет
    wa = _v(st, "write_action")
    tools = _v(st, "tools_table")
    if wa and tools is not None and not any(len(r) > 1 and "запис" in r[1].lower() for r in tools):
        add("write_tool", "tools_table", "warning",
            f"В {_c('write_action')} агент меняет внешнюю систему {_q(wa)}, но в {_c('tools_table')} нет ни одного "
            "инструмента записи. Чем агент сделает это действие?")

    # 5. Ядро агента не видно в To-Be как шаг агента
    core_step = _v(st, "core_step")
    tobe = _v(st, "to_be_table")
    if core_step and tobe:
        agent_rows = [r for r in tobe if len(r) > 1 and "агент" in r[1].lower()]
        if not agent_rows:
            add("core_in_tobe", "to_be_table", "caution",
                f"Ядро агента — {_q(core_step)}, а в To-Be нет ни одного шага с исполнителем «агент».")
        elif not any(_words(core_step) & _words(r[0]) for r in agent_rows):
            add("core_in_tobe", "to_be_table", "caution",
                f"Ядро агента — {_q(core_step)}, но среди шагов агента в To-Be его не видно: "
                + "; ".join(_q(r[0], 50) for r in agent_rows[:3]) + ".")

    # 6. Прогон дороже денежного потолка
    limits = _v(st, "limits_table") or []
    cap = next((_num(r[1]) for r in limits if len(r) > 1 and re.search(r"(?i)деньг|стоим|\$|cost|бюджет", r[0])), None)
    runs = _v(st, "runs_table") or []
    prices = [_num(r[3]) for r in runs if len(r) > 3 and _num(r[3]) is not None]
    if cap and prices and max(prices) > cap:
        add("run_over_cap", "limits_table", "warning",
            f"Самый дорогой прогон в {_c('runs_table')} стоит {max(prices):g} $, а денежный потолок прогона в "
            f"{_c('limits_table')} — {cap:g} $. Либо потолок не работает, либо прогоны шли без него.")

    # 7. Запуск без человека заявлен кнопкой
    trig = str(_v(st, "trigger") or "")
    if re.search(r"(?i)кнопк|вручную|захожу|открываю чат|запускаю сам", trig):
        add("manual_trigger", "trigger", "warning",
            f"Условие запуска в {_c('trigger')} {_q(trig)} требует человека. Это не автономность: нужен триггер — событие, "
            "расписание или внешний вызов.")

    # 8. Уровень агентности против паттерна
    lvl = str(_v(st, "agency_level") or "")
    pat = str(_v(st, "pattern") or "")
    simple = lvl.startswith(("Workflow", "Хватит правила", "Один вызов модели"))
    if simple and pat and pat not in ("Конвейер с валидатором", "Parallel — веер"):
        add("agency_vs_pattern", "pattern", "caution",
            f"В {_c('agency_level')} выбран уровень «{lvl}», а в {_c('pattern')} паттерн «{pat}», где решение принимается "
            "внутри прогона. Это уже агент — либо поменяйте уровень, либо упростите паттерн.")

    # 9. Утверждение человеком без показа исполняемого действия (точка остановки описывает описание, а не параметры)
    control = _v(st, "control_table") or []
    stop = str(_v(st, "stop_point") or "")
    if any(len(r) > 2 and "утверд" in r[2].lower() for r in control) and stop and BLIND.search(stop):
        add("approval_blind", "stop_point", "warning",
            f"В {_c('control_table')} есть точка «утвердить», но по ответу о точке остановки человек видит не исполняемое "
            f"действие: {_q(stop, 80)}. Подтверждение защищает, только если показано ровно то, что исполнится.")

    # 10. Испытания выбраны, а стенд не отделён
    target = str(_v(st, "attack_target") or "").lower()
    if _v(st, "attack_selection") and target and re.search(r"\bпрод\b|рабоч\w* баз", target) and "коп" not in target:
        add("attack_on_prod", "attack_target", "warning",
            f"Испытания выбраны, а место проведения {_q(target)} похоже на прод. Атаки — реальные запросы, нужен стенд с копией данных.")
    return out
