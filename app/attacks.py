"""Испытания агента атаками. Запуск — только после двух подтверждений.

Первое подтверждение — «снять предохранитель»: сервер выдаёт план (адрес, атаки, число запросов) и одноразовый ключ на 10 минут.
Второе подтверждение — «пуск»: человек вводит хост стенда вручную и отмечает, что это не прод.
Без обоих подтверждений ни один запрос к агенту не уходит."""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from datetime import datetime

from . import core, target as target_mod
from .state import add_risk, fmt_dt, project_dir

CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

ARM_TTL_SEC = 600


def _h(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


def plan(st: dict, selected: list[str]) -> dict:
    if not isinstance(selected, list) or not all(isinstance(x, str) for x in selected):
        raise ValueError("атаки передаются списком идентификаторов")
    t = target_mod.normalize(st.get("target") or {})
    defs = [a for a in core.attacks() if a["id"] in selected or a["choice"] in selected]
    if not defs:
        raise ValueError("не выбрано ни одной атаки")
    auto = [a for a in defs if not a.get("manual")]
    requests = sum(len(a["payloads"]) * int(a.get("repeat", 1)) for a in auto)
    return {"target": t["url"], "host": target_mod.host_label(t), "attacks": [
        {"id": a["id"], "title": a["title"], "manual": bool(a.get("manual")),
         "requests": 0 if a.get("manual") else len(a["payloads"]) * int(a.get("repeat", 1)), "why": a["why"]}
        for a in defs], "requests_total": requests,
        "warning": "Это реальные запросы к агенту. Они могут менять внешние системы и тратить деньги. Запускайте только на стенде."}


def arm(st: dict, selected: list[str]) -> dict:
    """Первое подтверждение: выдаёт план и ключ пуска."""
    p = plan(st, selected)
    token = secrets.token_urlsafe(16)
    # ключ хранится в состоянии проекта хешем: пуск возможен и из другого процесса (экран или командная строка)
    st["attack_arm"] = {"token_hash": _h(token), "selected": [a["id"] for a in p["attacks"]], "host": p["host"],
                        "target": dict(st.get("target") or {}), "expires": time.time() + ARM_TTL_SEC}
    return {**p, "token": token, "expires_in_sec": ARM_TTL_SEC,
            "second_confirmation": f"Чтобы запустить, введите хост стенда вручную: {p['host']}"}


def check_launch(st: dict, token: str, typed_host: str, not_prod: bool) -> dict:
    armed = st.get("attack_arm")
    if not armed or armed.get("token_hash") != _h(token):
        raise PermissionError("нет первого подтверждения или ключ пуска не от этого проекта")
    if time.time() > armed["expires"]:
        st.pop("attack_arm", None)
        raise PermissionError("ключ пуска просрочен — снимите предохранитель заново")
    if armed["target"] != dict(st.get("target") or {}):
        raise PermissionError("адрес агента изменился после первого подтверждения — снимите предохранитель заново")
    if (typed_host or "").strip() != armed["host"]:
        raise PermissionError("введённый хост не совпадает с адресом стенда")
    if not not_prod:
        raise PermissionError("не отмечено, что это стенд, а не прод")
    st.pop("attack_arm", None)  # ключ одноразовый
    return armed


def _evaluate(a: dict, results: list[dict]) -> tuple[str, list[str]]:
    det = a.get("detect") or {}
    problems = []
    # Стенд не отвечает вовсе — это не слабость агента, а непроведённое испытание. Отдельный вердикт,
    # иначе транспортная ошибка читается как «нужно усилить защиту».
    if results and all(r.get("error") for r in results):
        return "unreachable", [f"стенд недоступен, испытание не проведено: {results[0]['error']}"]
    for r in results:
        text = f"{r['answer']} {r['status']} {r['raw']}".lower()
        if r["error"]:
            problems.append(f"нет ответа: {r['error']}")
            continue
        if det.get("no_5xx") and r["status_code"] and r["status_code"] >= 500:
            problems.append(f"сервер упал: HTTP {r['status_code']}")
        for bad in det.get("must_not_contain", []):
            if bad.lower() in text:
                problems.append(f"в ответе есть «{bad}»")
        if det.get("max_seconds") and r["seconds"] > det["max_seconds"]:
            problems.append(f"ответ шёл {r['seconds']} с при пределе {det['max_seconds']} с")
    if det.get("must_contain_any"):
        joined = " ".join(f"{r['answer']} {r['status']} {r['raw']}".lower() for r in results)
        if not any(x.lower() in joined for x in det["must_contain_any"]):
            problems.append("не найдено признака ожидаемого контроля: " + ", ".join(det["must_contain_any"][:4]))
    if a.get("needs_action") and not problems:
        acted = [r for r in results if not r["error"] and r["status"].lower() not in ("stopped_by_limit", "rejected")
                 and (r["answer"] or "").strip()]
        if not acted:
            return "inconclusive", ["агент ни разу не обработал вход (предел, отказ или пустой ответ) — "
                                    "устойчивость к атаке не доказана"]
    if det.get("same_answer") and len(results) >= 2:
        last = results[-1]
        if "already" not in last["raw"].lower() and "уже" not in last["raw"].lower() and results[0]["answer"] != last["answer"]:
            problems.append("повтор с тем же ключом дал другой результат — возможно второе действие")
    if not results:
        return "manual", []
    return ("passed" if not problems else "failed"), problems


def launch(st: dict, token: str, typed_host: str, not_prod: bool, transport=None) -> dict:
    """Второе подтверждение и прогон. Пишет протокол испытаний и находки в риски."""
    armed = check_launch(st, token, typed_host, not_prod)
    run = execute(armed, transport)
    return record(st, run)


def execute(armed: dict, transport=None) -> dict:
    """Сами запросы к агенту. Состояние проекта не трогает — его можно вызывать вне замка."""
    t = target_mod.normalize(armed["target"])
    run = {"ts": datetime.now().isoformat(timespec="seconds"), "target": t["url"], "results": []}
    for a in core.attacks():
        if a["id"] not in armed["selected"]:
            continue
        if a.get("manual"):
            run["results"].append({"id": a["id"], "title": a["title"], "verdict": "manual",
                                   "problems": [], "calls": [], "instructions": a.get("instructions", "")})
            continue
        calls = []
        for payload in a["payloads"]:
            q = "x" * 300_000 if payload == "@@BIG@@" else payload
            for i in range(int(a.get("repeat", 1))):
                key = f"copilot-attack-{a['id']}" if a.get("repeat") else None
                r = target_mod.call(t, q, key=key, transport=transport)
                r["payload"] = (payload[:200] + "…") if len(payload) > 200 else payload
                calls.append(r)
        verdict, problems = _evaluate(a, calls)
        run["results"].append({"id": a["id"], "title": a["title"], "verdict": verdict, "problems": problems,
                               "level": a["level"],
                               "calls": [{k: c[k] for k in ("payload", "status_code", "seconds", "status", "answer", "error")}
                                         for c in calls]})
    return run


def record(st: dict, run: dict) -> dict:
    """Итоги прогона — в риски, историю и протокол. Вызывается под замком на свежем состоянии."""
    for r in run["results"]:
        if r["verdict"] == "failed":
            add_risk(st, core.qstep("attack_selection"), f"Испытание «{r['title']}» показало, что нужно усилить: {'; '.join(r['problems'])[:300]}", r["level"], "attack")
        elif r["verdict"] == "inconclusive":
            add_risk(st, core.qstep("attack_selection"), f"Испытание «{r['title']}» не доказано: {'; '.join(r['problems'])[:300]}", "caution", "attack")
        elif r["verdict"] == "unreachable":
            add_risk(st, core.qstep("attack_selection"), f"Испытание «{r['title']}» не проведено: стенд недоступен", "caution", "attack")
    st.setdefault("attack_runs", []).append({k: run[k] for k in ("ts", "target")} | {
        "summary": {r["id"]: r["verdict"] for r in run["results"]}})
    path = write_protocol(st, run)
    run["protocol"] = str(path)
    return run


def _clean(s) -> str:
    """Управляющие символы из мусорных входов не попадают в протокол: файл остаётся текстом."""
    return CTRL.sub("·", str(s or "")).replace("`", "'")


def write_protocol(st: dict, run: dict):
    d = project_dir(st["id"]) / "docs"
    d.mkdir(parents=True, exist_ok=True)
    stamp = run["ts"].replace(":", "-")
    lines = [f"# Протокол испытаний — {st['name']}", "", f"Дата: {fmt_dt(run['ts'])}. Адрес стенда: {run['target']}.", "",
             "| Испытание | Итог | Что найдено |", "|---|---|---|"]
    names = {"passed": "пройдено", "failed": "нужно усилить", "manual": "вручную", "inconclusive": "не доказано",
             "unreachable": "не проверено: стенд недоступен"}
    for r in run["results"]:
        lines.append(f"| {r['title']} | {names[r['verdict']]} | {'; '.join(r['problems']) or '—'} |")
    for r in run["results"]:
        lines += ["", f"## {r['title']}", ""]
        if r["verdict"] == "manual":
            lines.append(r.get("instructions", ""))
            continue
        for c in r["calls"]:
            lines.append(f"- Вход: `{_clean(c['payload'][:120])}` → HTTP {c['status_code']}, {c['seconds']} с, "
                         f"статус «{_clean(c['status']) or '—'}»; ответ: {_clean(c['answer'][:300] or c['error']) or '—'}")
    code = core.code(core.qstep("attack_selection"))
    lines += ["", f"Что делать дальше: каждое «нужно усилить» уже стоит рекомендацией шага {code}. После доработки "
              "прогоните испытание повторно — так находка закрывается."]
    path = d / f"{code} Протокол испытаний {datetime.fromisoformat(run['ts']).strftime('%d.%m.%Y %H-%M-%S')}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
