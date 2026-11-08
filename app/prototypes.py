"""Прототипы из ответов проекта: схема «как работает агент», скелет сценария n8n, каркас агента на Python.

Всё строится детерминированно из ответов — без вызова модели. Пустые места помечаются, а не выдумываются."""
from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from . import config, core
from .state import get_answer, project_dir

UNSET = "не задано"


def _val(st, qid):
    """Ответ на вопрос ядра — шаг вопроса берётся из ядра, номера шагов в коде не держатся."""
    rec = get_answer(st, core.qstep(qid), qid)
    if not rec or rec["status"] not in ("answered", "needs_detail"):
        return None
    return rec["value"]


def _label(text, n=38) -> str:
    if not text:
        return UNSET
    t = re.sub(r"[\[\]{}()<>\"|#;`]", " ", str(text))
    t = re.sub(r"\s+", " ", t).strip()
    return (t[: n - 1] + "…") if len(t) > n else t


def _first(rows, pred=None, col=0):
    for r in rows or []:
        if pred is None or pred(r):
            return r[col] if col < len(r) else None
    return None


def parse_limits(rows) -> dict:
    """Читает лист потолков (вопрос limits_table): первое число строки, вид потолка — по названию."""
    out = {}
    for r in rows or []:
        name = (r[0] if r else "").lower()
        m = re.search(r"\d+(?:[.,]\d+)?", (r[1] if len(r) > 1 else "").replace(" ", ""))
        if not m:
            continue
        v = float(m.group().replace(",", "."))
        if any(k in name for k in ("шаг", "вызов", "итерац", "step")):
            out["max_steps"] = int(v)
        elif any(k in name for k in ("деньг", "стоим", "$", "cost", "бюджет")):
            out["max_cost_usd"] = v
        elif any(k in name for k in ("врем", "мин", "сек", "time")):
            unit = (r[1] if len(r) > 1 else "").lower()
            out["max_seconds"] = int(v * 60) if "мин" in unit or "мин" in name else int(v)
        elif any(k in name for k in ("артефакт", "запис", "файл")):
            out["max_artifacts"] = int(v)
        elif any(k in name for k in ("размер", "знак", "символ", "вход")):
            out["max_input_chars"] = int(v)
    return out


def placeholders(st) -> dict:
    tools = _val(st, "tools_table") or []
    control = _val(st, "control_table") or []
    limits = _val(st, "limits_table") or []
    lim = parse_limits(limits)
    names = {"max_steps": "шагов", "max_cost_usd": "$", "max_seconds": "сек", "max_artifacts": "записей",
             "max_input_chars": "знаков"}
    lim_text = ", ".join(f"{names.get(k, k)} {v:g}" if isinstance(v, float) else f"{names.get(k, k)} {v}"
                         for k, v in lim.items()) if lim else None
    human_role = _first(control, lambda r: len(r) > 3 and r[2] and "нет" not in r[2].lower(), 3)
    return {
        "trigger": _label(_val(st, "trigger"), 30),
        "core": _label(_val(st, "core_step"), 34),
        "read": _label(_first(tools, lambda r: len(r) > 1 and "чтен" in r[1].lower(), 0) or _first(tools, None, 0), 26),
        "write": _label(_first(tools, lambda r: len(r) > 1 and "запис" in r[1].lower(), 0), 26),
        "human": _label(human_role or _val(st, "human_left"), 30),
        "limit": _label(lim_text, 30),
        "queue": _label(_val(st, "escalation"), 28),
        "gate": _label(_val(st, "quality_gate"), 30),
    }


def approval_before_write(st) -> str | None:
    """Роль, которая утверждает действие до записи: точка «утвердить» в контуре управления или инструмент «повтор недопустим»."""
    for r in _val(st, "control_table") or []:
        if len(r) > 2 and "утверд" in (r[2] or "").lower():
            return (r[3] if len(r) > 3 and r[3] else "человек")
    for r in _val(st, "tools_table") or []:
        if len(r) > 4 and "недопуст" in (r[4] or "").lower():
            return f"человек — {r[0]}"
    return None


def cron_from_text(text: str) -> tuple[str, bool]:
    """Расписание из ответа: «каждые N минут», «каждый час», «в 9:30 по рабочим дням». Второе — понято ли."""
    import re as _re
    s = (text or "").lower()
    m = _re.search(r"кажды[ей]\s+(\d+)\s*мин", s)
    hours = _re.search(r"с\s*(\d{1,2})[:.]?\d{0,2}\s*до\s*(\d{1,2})", s)
    hpart = f"{hours.group(1)}-{hours.group(2)}" if hours else "*"
    days = "1-5" if "рабоч" in s or "будн" in s else "*"
    if m:
        return f"*/{int(m.group(1))} {hpart} * * {days}", True
    if _re.search(r"кажд\w*\s+час", s):
        return f"0 {hpart} * * {days}", True
    at = _re.search(r"в\s*(\d{1,2})[:.](\d{2})", s)
    if at:
        return f"{int(at.group(2))} {int(at.group(1))} * * {days}", True
    return "30 9 * * 1-5", False


def agent_diagram(st) -> dict:
    choice = _val(st, "pattern")
    cmap = core._load("patterns.yaml")["choice_map"]
    pid = cmap.get(choice, "single")
    card = next(p for p in core.patterns() if p["id"] == pid)
    ph = placeholders(st)
    mer = card["mermaid"]
    for k, v in ph.items():
        mer = mer.replace("{" + k + "}", v)
    missing = [k for k, v in ph.items() if v == UNSET]
    approver = approval_before_write(st)
    if approver:
        mer += f"\n  HA[/Утверждает до записи: {_label(approver, 30)}/] -. да .-> W"
    return {"pattern": pid, "pattern_chosen": bool(choice), "card": {k: card[k] for k in
            ("title", "who_decides", "limit", "after", "when", "not_when")}, "mermaid": mer, "missing": missing}


def n8n_workflow(st) -> dict:
    """Скелет сценария n8n для наглядности и быстрых проб. Импорт: Workflows → Import from File."""
    trig_text = (_val(st, "trigger") or "").lower()
    tools = _val(st, "tools_table") or []
    lim = parse_limits(_val(st, "limits_table"))
    max_iter = int(lim.get("max_steps", 10))
    nodes, conns = [], {}

    def node(name, ntype, version, pos, params=None, **extra):
        n = {"id": str(uuid.uuid4()), "name": name, "type": ntype, "typeVersion": version, "position": pos,
             "parameters": params or {}}
        n.update(extra)
        nodes.append(n)
        return name

    def link(a, b, kind="main", out_index=0):
        conns.setdefault(a, {}).setdefault(kind, [])
        while len(conns[a][kind]) <= out_index:
            conns[a][kind].append([])
        conns[a][kind][out_index].append({"node": b, "type": kind, "index": 0})

    scheduled = any(k in trig_text for k in ("распис", "cron", "кажд", "в 0", "в 1", "утр"))
    cron, cron_ok = cron_from_text(trig_text)
    if scheduled:
        sched = node("Расписание", "n8n-nodes-base.scheduleTrigger", 1.2, [-220, 300],
                     {"rule": {"interval": [{"field": "cronExpression", "expression": cron}]}})
        # у расписания нет входа — задание агенту задаётся здесь, иначе «Проверка входа» всегда уводит в отказ
        t = node("Задание по расписанию", "n8n-nodes-base.set", 3.4, [0, 300], {
            "assignments": {"assignments": [{"id": str(uuid.uuid4()), "name": "query", "type": "string",
                                             "value": "Выполни шаг: " + (_val(st, "core_step") or UNSET)}]},
            "options": {}})
        link(sched, t)
    else:
        t = node("Входящий вызов", "n8n-nodes-base.webhook", 2, [0, 300],
                 {"httpMethod": "POST", "path": "agent-run", "responseMode": "lastNode"}, webhookId=str(uuid.uuid4()))
    prep = node("Проверка входа", "n8n-nodes-base.if", 2, [220, 300], {
        "conditions": {"options": {"caseSensitive": True, "typeValidation": "loose"}, "combinator": "and",
                       "conditions": [{"id": str(uuid.uuid4()), "leftValue": "={{ ($json.body?.query ?? $json.query ?? '').length }}",
                                       "rightValue": 0, "operator": {"type": "number", "operation": "gt"}}]}})
    agent = node("Агент", "@n8n/n8n-nodes-langchain.agent", 1.7, [460, 280], {
        "promptType": "define", "text": "={{ $json.body?.query ?? $json.query }}",
        "options": {"systemMessage": "Ты выполняешь шаг: " + (_val(st, "core_step") or UNSET) +
                    ". Если не уверен или нарушается правило — верни статус queued и причину. Не выдумывай данные.",
                    "maxIterations": max_iter}}, onError="stopWorkflow")
    model = node("Модель (OpenAI-совместимая)", "@n8n/n8n-nodes-langchain.lmChatOpenAi", 1.2, [380, 520],
                 {"model": {"__rl": True, "mode": "id", "value": "qwen3.8-27b"}, "options": {"temperature": 0.2}})
    link(model, agent, "ai_languageModel")
    for i, r in enumerate(tools[:5]):
        name = _label(r[0] if r else f"Инструмент {i + 1}", 40)
        tnode = node(f"Инструмент: {name}", "@n8n/n8n-nodes-langchain.toolHttpRequest", 1.1, [540 + i * 160, 520],
                     {"toolDescription": f"{name}. Права: {r[3] if len(r) > 3 else UNSET}. Повтор: {r[4] if len(r) > 4 else UNSET}",
                      "method": "POST" if len(r) > 1 and "запис" in r[1].lower() else "GET",
                      "url": "https://example.invalid/заменить-на-адрес-системы"})
        link(tnode, agent, "ai_tool")
    stop = node("Остановлено по пределу?", "n8n-nodes-base.if", 2, [720, 280], {
        "conditions": {"options": {"caseSensitive": False, "typeValidation": "loose"}, "combinator": "or",
                       "conditions": [{"id": str(uuid.uuid4()), "leftValue": "={{ $json.output }}",
                                       "rightValue": "queued", "operator": {"type": "string", "operation": "contains"}},
                                      {"id": str(uuid.uuid4()), "leftValue": "={{ $json.output }}",
                                       "rightValue": "предел", "operator": {"type": "string", "operation": "contains"}}]}})
    queue = node("Ручная очередь", "n8n-nodes-base.noOp", 1, [960, 180])
    write = node("Запись результата", "n8n-nodes-base.httpRequest", 4.2, [960, 380],
                 {"method": "POST", "url": "https://example.invalid/заменить-на-систему-записи",
                  "sendBody": True, "specifyBody": "json", "jsonBody": "={{ { result: $json.output } }}"})
    reject = node("Отказ: пустой вход", "n8n-nodes-base.noOp", 1, [460, 120])
    link(t, prep)
    link(prep, agent, out_index=0)
    link(prep, reject, out_index=1)
    link(agent, stop)
    link(stop, queue, out_index=0)
    link(stop, write, out_index=1)
    wf = {"name": f"Прототип — {st['name']}", "nodes": nodes, "connections": conns, "active": False,
          "settings": {"executionOrder": "v1", "errorWorkflow": ""},
          "meta": {"note": "Скелет для наглядности. Адреса систем, модель и ключи задаются при импорте. "
                           "Сценарий обработки ошибок назначьте в settings.errorWorkflow."
                           + ("" if (not scheduled or cron_ok) else
                              " Расписание из ответа не распознано — поставлено 9:30 по рабочим дням, поправьте.")}}
    return wf


TEMPLATE = config.TEMPLATES_DIR / "python_agent"


def llm_endpoints(st) -> list[dict]:
    """Модели каркаса по решению о типе тайны (secret_kind): облачная запасная точка — только если тайны в данных и процессе нет."""
    local = {"base_url": "http://localhost:1234/v1", "model": "qwen3.8-27b", "key_env": "", "price_in": 0, "price_out": 0}
    cloud = {"base_url": "https://openrouter.ai/api/v1", "model": "qwen/qwen-plus", "key_env": "OPENROUTER_API_KEY",
             "price_in": 0.4, "price_out": 1.2}
    secret = (_val(st, "secret_kind") or "").lower()
    if secret.startswith("тайны нет"):
        return [local, cloud]
    return [local]  # тайна в процессе или в данных, либо решение не принято — наружу ничего не уходит


def python_skeleton(st) -> list[str]:
    out_dir = project_dir(st["id"]) / "prototype" / "python_agent"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for f in TEMPLATE.rglob("*"):
        if f.is_dir() or "__pycache__" in f.parts:
            continue
        rel = f.relative_to(TEMPLATE)
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if rel.name == "tools.py" and dst.exists():
            continue  # инструменты Junior Pilot дописывает сам — не перетираем
        shutil.copyfile(f, dst)
        written.append(rel.as_posix())
    lim = {"max_steps": 12, "max_cost_usd": 0.5, "max_seconds": 300, "max_artifacts": 20, "max_input_chars": 200000}
    lim.update(parse_limits(_val(st, "limits_table")))
    tools = []
    for r in _val(st, "tools_table") or []:
        rep = (r[4] if len(r) > 4 else "").lower()
        kind = "confirm" if "недопуст" in rep else ("idempotent" if "ключ" in rep or "идемпот" in rep else "safe")
        tools.append({"name": re.sub(r"\W+", "_", (r[0] or "tool").lower()).strip("_")[:40] or "tool",
                      "title": r[0], "access": r[1] if len(r) > 1 else "", "system": r[2] if len(r) > 2 else "",
                      "rights": r[3] if len(r) > 3 else "", "repeat_class": kind})
    cfg = {
        "project": st["name"],
        "core_step": _val(st, "core_step") or UNSET,
        "success_criterion": _val(st, "success_criterion") or UNSET,
        "limits": lim,
        "tools": tools,
        "llm": {"endpoints": llm_endpoints(st)},
    }
    (out_dir / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append("config.json")
    return written


def build_all(st) -> dict:
    d = project_dir(st["id"]) / "prototype"
    d.mkdir(parents=True, exist_ok=True)
    diag = agent_diagram(st)
    (d / "схема агента.mmd").write_text(diag["mermaid"], encoding="utf-8")
    wf = n8n_workflow(st)
    (d / "прототип n8n.json").write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
    files = python_skeleton(st)
    return {"diagram": diag, "n8n": wf, "python_files": files, "folder": str(d)}
