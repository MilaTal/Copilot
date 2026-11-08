"""Прогон набора проверок (вопрос eval_table, шаг «Проверка и атаки») по адресу агента и регрессия «до / после».

Судья «код» понимает простые признаки прохождения:
  содержит «X» | не содержит «X» | status == X | статус X | clean == true (ищется в ответе как есть)
Остальное — судья «человек»: результат показывается, отметку ставит инженер."""
from __future__ import annotations

import re
from datetime import datetime

from . import core, target as target_mod
from .state import add_risk, get_answer

QUOTED = re.compile(r"[«\"']([^»\"']+)[»\"']")


def code_judge(criterion: str, r: dict) -> str | None:
    c = (criterion or "").strip().lower()
    text = f"{r['answer']} {r['status']} {r['raw']}".lower()
    m = QUOTED.search(criterion or "")
    if c.startswith("не содержит") and m:
        return "pass" if m.group(1).lower() not in text else "fail"
    if c.startswith("содержит") and m:
        return "pass" if m.group(1).lower() in text else "fail"
    m2 = re.match(r"(status\s*==|статус)\s*[«\"']?([\w-]+)", c)
    if m2:
        return "pass" if r["status"].lower() == m2.group(2) else "fail"
    return None


def cost_of_success(rows: list[list[str]]) -> dict:
    """Стоимость успешного результата из серии прогонов: сумма цен ÷ число успешных."""
    total, ok, n = 0.0, 0, 0
    for r in rows or []:
        if len(r) < 4:
            continue
        m = re.search(r"\d+(?:[.,]\d+)?", r[3].replace(" ", ""))
        if not m:
            continue
        n += 1
        total += float(m.group().replace(",", "."))
        if r[2].strip().lower() in ("да", "yes", "+", "успешно", "1"):
            ok += 1
    return {"runs": n, "successful": ok, "total_usd": round(total, 4),
            "per_success_usd": round(total / ok, 4) if ok else None,
            "failed_share": round((n - ok) / n, 3) if n else None}


def run(st: dict, transport=None) -> dict:
    rec = get_answer(st, core.qstep("eval_table"), "eval_table")
    if not rec or rec["status"] != "answered":
        raise ValueError(f"набор проверок шага {core.code(core.qstep('eval_table'))} не заполнен")
    t = target_mod.normalize(st.get("target") or {})
    results = []
    for i, row in enumerate(rec["value"], 1):
        kind, inp, expected, crit, judge = (row + [""] * 5)[:5]
        r = target_mod.call(t, inp, transport=transport)
        verdict = code_judge(crit, r) if "код" in judge.lower() or not judge else None
        results.append({"n": i, "type": kind, "input": inp[:300], "expected": expected, "criterion": crit,
                        "judge": judge or "код", "answer": r["answer"][:600], "status": r["status"],
                        "seconds": r["seconds"], "cost_usd": r["cost_usd"], "error": r["error"],
                        "verdict": verdict or ("fail" if r["error"] else "manual")})
    auto = [x for x in results if x["verdict"] in ("pass", "fail")]
    summary = {"ts": datetime.now().isoformat(timespec="seconds"), "total": len(results),
               "passed": sum(1 for x in results if x["verdict"] == "pass"),
               "failed": sum(1 for x in results if x["verdict"] == "fail"),
               "manual": sum(1 for x in results if x["verdict"] == "manual"),
               "auto_share": round(len(auto) / len(results), 2) if results else 0,
               "cost_usd": round(sum(x["cost_usd"] or 0 for x in results), 4)}
    return record(st, {"summary": summary, "results": results})


def execute(st: dict, transport=None) -> dict:
    """Только запросы к агенту, без записи в состояние: вызывается вне замка на снимке проекта."""
    snapshot = {"answers": st.get("answers"), "target": st.get("target")}
    out = run(snapshot, transport=transport)
    return {"summary": out["summary"], "results": out["results"]}


def record(st: dict, res: dict) -> dict:
    """Итоги прогона — в историю и риски. Вызывается под замком на свежем состоянии."""
    summary, results = res["summary"], res["results"]
    st.setdefault("eval_runs", []).append({"summary": summary, "results": results})
    st["eval_runs"] = st["eval_runs"][-20:]
    if summary["failed"]:
        add_risk(st, core.qstep("eval_table"), f"Проверка качества: не пройдено {summary['failed']} из {summary['total']} кейсов "
                         f"({summary['ts']})", "warning", "eval")
    return {"summary": summary, "results": results, "regression": regression(st)}


def regression(st: dict) -> dict | None:
    runs = st.get("eval_runs") or []
    if len(runs) < 2:
        return None
    before, after = runs[-2], runs[-1]
    b = {x["input"]: x["verdict"] for x in before["results"]}
    changes = []
    for x in after["results"]:
        prev = b.get(x["input"])
        if prev and prev != x["verdict"]:
            changes.append({"input": x["input"][:120], "before": prev, "after": x["verdict"]})
    return {"before": before["summary"], "after": after["summary"], "changes": changes,
            "same_set": set(b) == {x["input"] for x in after["results"]}}
