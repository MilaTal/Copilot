"""Готовность шагов и табло рисков. Ничего не блокирует: только считает и подсвечивает.

Шаг «готов», только если все обязательные вопросы закрыты полными ответами и в шаге нет критичных рисков —
в том числе от испытаний, проверок и разбора разработки. Готовность к проду падает от критичных рисков."""
from __future__ import annotations

from . import consistency, core
from .state import get_answer, missing_required_rows

REASONS = {
    "unknown": "Ответ «Не знаю»",
    "skipped": "Сознательно опущено",
    "needs_detail": "Senior Pilot подсказывает, что добавить",
    "missing": "Нет ответа",
    "few_rows": "Мало строк",
    "required_rows": "Нет обязательной строки",
    "risky_option": "Выбран рискованный вариант",
    "not_run": "Не проверено на деле",
    "consistency": "Расхождение между шагами",
}


def _rows_ok(q: dict, rec: dict | None) -> bool:
    if q["type"] != "table" or not rec or rec["status"] != "answered":
        return True
    return len(rec.get("value") or []) >= int(q.get("min_rows") or 1)


def _req_missing(q: dict, rec: dict | None) -> list[str]:
    """Обязательные строки таблицы (required_rows), которых в отвеченной таблице нет."""
    if q["type"] != "table" or not q.get("required_rows") or not rec or rec["status"] != "answered":
        return []
    return missing_required_rows(q, rec.get("value"))


def _risky(q: dict, rec: dict | None) -> str | None:
    if not rec or rec["status"] != "answered" or not q.get("risky_options"):
        return None
    vals = rec["value"] if isinstance(rec["value"], list) else [rec["value"]]
    hit = [v for v in vals if v in q["risky_options"]]
    return hit[0] if hit else None


def _touched(st: dict, step_id: int) -> bool:
    return bool((st.get("answers") or {}).get(str(step_id)))


def risks(st: dict) -> list[dict]:
    """Риски из незакрытых и рискованных ответов начатых шагов, из непроведённых проверок,
    плюс добавленные Senior Pilot, испытаниями, проверками, разбором разработки и вручную."""
    out = []
    owners = st.get("risk_owners") or {}
    for s in core.steps():
        touched = _touched(st, s["id"])
        for q in s["questions"]:
            rec = get_answer(st, s["id"], q["id"])
            status = rec["status"] if rec else "missing"
            reason, text, level = None, q["risk"]["text"], q["risk"]["level"]
            if status in ("unknown", "skipped"):
                reason = status
            elif status == "needs_detail":
                reason = status
                follow = (rec.get("senior") or {}).get("follow_up") or "ответ слишком общий"
                text, level = f"Ответ требует уточнения: {follow}", "caution"
            elif status == "missing" and touched and q.get("required", True):
                reason, level = "missing", "caution"
            elif status == "answered" and not _rows_ok(q, rec):
                reason, level = "few_rows", "caution"
                text = f"В таблице строк {len(rec['value'])}, нужно не меньше {q.get('min_rows')}."
            elif _req_missing(q, rec):
                reason, level = "required_rows", "caution"
                text = "В таблице нет обязательной строки: " + "; ".join(f"«{x}»" for x in _req_missing(q, rec)) + "."
            elif _risky(q, rec):
                reason = "risky_option"
                text = f"Выбран вариант «{_risky(q, rec)}». {q['risk']['text']}"
            if not reason:
                continue
            rid = f"q:{s['id']}:{q['id']}"
            out.append({"id": rid, "step": s["id"], "code": s["code"], "qid": q["id"], "level": level,
                        "text": text, "reason": REASONS[reason], "question": q["text"],
                        "owner": owners.get(rid, ""), "source": "вопрос"})
    # проверки на деле: вопросы описаны, а прогона не было. Шаг — тот, где в ядре живут атаки и набор проверок.
    atk = core.qstep("attack_selection")
    atk_touched = any(k in core.segments(atk)[core.segment_index(atk, "attack_selection") - 1]["questions"]
                      for k in ((st.get("answers") or {}).get(str(atk)) or {}))
    if atk_touched and not st.get("attack_runs"):
        out.append({"id": "run:attacks", "step": atk, "code": core.code(atk), "qid": None, "level": "caution",
                    "text": "Испытания ещё не прогонялись — ответы шага описывают намерение, а не результат.",
                    "reason": REASONS["not_run"], "question": "", "owner": owners.get("run:attacks", ""), "source": "прогон"})
    ev = core.qstep("eval_table")
    if _touched(st, ev) and (st.get("target") or {}).get("url") and not st.get("eval_runs"):
        out.append({"id": "run:eval", "step": ev, "code": core.code(ev), "qid": None, "level": "caution",
                    "text": "Набор проверок ни разу не прогонялся по адресу агента.",
                    "reason": REASONS["not_run"], "question": "", "owner": owners.get("run:eval", ""), "source": "прогон"})
    for c in consistency.check(st):
        out.append({"id": c["id"], "step": c["step"], "code": core.step(c["step"])["code"], "qid": None,
                    "level": c["level"], "text": c["text"], "reason": REASONS["consistency"], "question": "",
                    "owner": owners.get(c["id"], ""), "source": "consistency"})
    for r in st.get("risks_extra") or []:
        if r.get("status") == "open":
            code = core.step(r["step"])["code"]
            out.append({"id": r["id"], "step": r["step"], "code": code, "qid": None, "level": r["level"],
                        "text": r["text"], "reason": {"senior": "Замечание Senior Pilot", "attack": "Находка испытаний",
                                                      "eval": "Находка проверки", "workspace": "Находка в разработке"}
                        .get(r["source"], "Добавлено вручную"),
                        "question": "", "owner": owners.get(r["id"], r.get("owner", "")), "source": r["source"]})
    return out


def step_status(st: dict, s: dict, rs: list[dict] | None = None) -> dict:
    rs = risks(st) if rs is None else rs
    mine = [r for r in rs if r["step"] == s["id"]]
    req = [q for q in s["questions"] if q.get("required", True)]
    closed, full, open_items = 0, 0, []
    touched = _touched(st, s["id"])
    for q in s["questions"]:
        rec = get_answer(st, s["id"], q["id"])
        status = rec["status"] if rec else "missing"
        if q.get("required", True):
            if status in core.CLOSED_STATUSES:
                closed += 1
            if status == "answered" and _rows_ok(q, rec) and not _risky(q, rec) and not _req_missing(q, rec):
                full += 1
        if status == "answered" and not _rows_ok(q, rec):
            open_items.append({"qid": q["id"], "text": q["text"], "reason": "few_rows",
                               "detail": f"строк {len(rec['value'])}, нужно не меньше {q.get('min_rows')}"})
        elif status == "answered" and _req_missing(q, rec):
            open_items.append({"qid": q["id"], "text": q["text"], "reason": "required_rows",
                               "detail": "нет строки: " + "; ".join(_req_missing(q, rec))})
        elif status != "answered" and (q.get("required", True) or status != "missing"):
            open_items.append({"qid": q["id"], "text": q["text"], "reason": status})
    total = len(req) or 1
    art = (st.get("artifacts") or {}).get(str(s["id"]))
    can_build = closed == len(req)
    has_warning = any(r["level"] == "warning" for r in mine)
    if not touched and not mine:
        label = "not_started"
    elif can_build and full == len(req) and not mine:
        label = "ready"
    elif can_build:
        label = "closed_with_risks"
    else:
        label = "in_progress"
    quality = full / total * (0.5 if has_warning else 1.0)
    return {
        "step": s["id"], "code": s["code"], "title": s["title"],
        "required": len(req), "closed": closed, "full": full,
        "closed_pct": round(closed / total * 100), "quality_pct": round(quality * 100),
        "can_build": can_build, "artifact": bool(art), "artifact_stale": bool(art and art.get("stale")),
        "label": label, "open": open_items, "warnings": sum(1 for r in mine if r["level"] == "warning"),
    }


def all_status(st: dict) -> list[dict]:
    rs = risks(st)
    return [step_status(st, s, rs) for s in core.steps()]


def annunciator(st: dict) -> list[dict]:
    """Табло рекомендаций: по лампе на шаг. warning — «важно», caution — «на развитие», off — чисто, idle — не начат.
    Советы Senior Pilot лампы не зажигают — это подсказки, а не пропуски."""
    rs = [r for r in risks(st) if r["source"] != "senior"]
    lamps = []
    for s, ss in zip(core.steps(), all_status(st)):
        mine = [r for r in rs if r["step"] == s["id"]]
        if any(r["level"] == "warning" for r in mine):
            light = "warning"
        elif mine:
            light = "caution"
        elif ss["label"] == "not_started":
            light = "idle"
        else:
            light = "off"
        lamps.append({"step": s["id"], "code": s["code"], "title": s["title"], "light": light, "count": len(mine)})
    return lamps


def overall(st: dict) -> dict:
    ss = all_status(st)
    n = len(ss)
    # готовность к проду: полнота ответов шага (80 %, вдвое меньше при критичном риске в шаге) и свежий документ (20 %)
    prod = sum(0.8 * x["quality_pct"] / 100 + 0.2 * (1 if x["artifact"] and not x["artifact_stale"] else 0) for x in ss) / n
    rs = [r for r in risks(st) if r["source"] != "senior"]
    return {
        "prod_readiness_pct": round(prod * 100),
        "closed_pct": round(sum(x["closed_pct"] for x in ss) / n),
        "steps_ready": sum(1 for x in ss if x["label"] == "ready"),
        "steps_started": sum(1 for x in ss if x["label"] != "not_started"),
        "warnings": sum(1 for r in rs if r["level"] == "warning"),
        "cautions": sum(1 for r in rs if r["level"] == "caution"),
        "advice": sum(1 for r in risks(st) if r["source"] == "senior"),
    }


BUILD_RULE = ("Документ шага собирается, когда у каждого обязательного вопроса есть ответ, «Не знаю» или «Опускаю».")
BUILD_HOW_TAIL = ("если данных нет — отметьте вопрос «Не знаю» или «Опускаю» с причиной в комментарии. Тогда документ "
                  "соберётся, а вопрос уйдёт в раздел «Рекомендации для развития».")


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def build_blockers(st: dict, step_id: int) -> list[dict]:
    """Обязательные вопросы шага, которые держат сборку документа: нет ответа или ответ ждёт уточнения.
    У «нужно уточнить» — подсказка Senior Pilot (follow_up), если она есть."""
    s = core.step(step_id)
    out = []
    for q in s["questions"]:
        if not q.get("required", True):
            continue
        rec = get_answer(st, s["id"], q["id"])
        status = rec["status"] if rec else "missing"
        if status in core.CLOSED_STATUSES:
            continue
        follow = (((rec or {}).get("senior") or {}).get("follow_up") or "") if status == "needs_detail" else ""
        out.append({"qid": q["id"], "text": q["text"], "reason": status, "follow_up": follow})
    return out


def build_block_message(st: dict, step_id: int) -> str:
    """Почему документ шага не собирается — каждая причина словами и что с ней сделать. Пусто — собирается."""
    s = core.step(step_id)
    ss = step_status(st, s)
    if ss["can_build"]:
        return ""
    parts = []
    blockers = build_blockers(st, step_id)
    for b in blockers:
        q = f"«{_short(b['text'], 140)}»"
        if b["reason"] == "needs_detail":
            parts.append(f"нужно уточнить: {q}"
                         + (f" — {_short(b['follow_up'], 300).rstrip('.;')}" if b["follow_up"] else ""))
        elif b["reason"] == "missing":
            parts.append(f"нет ответа: {q}")
        else:
            parts.append(f"не закрыт ({b['reason']}): {q}")
    also = []
    for o in ss["open"]:  # таблицы с недобором сборку не держат, но человек должен видеть их сразу
        if o["reason"] == "few_rows":
            also.append(f"мало строк: «{_short(o['text'], 140)}» ({o['detail']})")
        elif o["reason"] == "required_rows":
            also.append(f"нет обязательной строки: «{_short(o['text'], 140)}» ({o['detail']})")
    msg = (f"Документ шага {s['code']} пока не собирается: не закрыто обязательных вопросов — "
           f"{ss['required'] - ss['closed']} из {ss['required']}. " + "; ".join(parts) + ". ")
    if also:
        msg += "Сборку не держат, но уйдут в рекомендации: " + "; ".join(also) + ". "
    todo = []
    if any(b["reason"] == "needs_detail" for b in blockers):
        todo.append("доработайте ответ по подсказке Senior Pilot и отправьте его снова")
    if any(b["reason"] == "missing" for b in blockers):
        todo.append("ответьте на вопрос")
    return msg + "Что сделать: " + "; ".join(todo + [BUILD_HOW_TAIL]) + " " + BUILD_RULE


def step_debrief(st: dict, step_id: int) -> dict:
    """Итог шага для показа после работы: что не закрыто и какие риски. Не блокирует переход."""
    s = core.step(step_id)
    rs_all = risks(st)
    ss = step_status(st, s, rs_all)
    rs = [r for r in rs_all if r["step"] == int(step_id)]
    return {"status": ss, "risks": rs, "ready": [{"text": x} for x in s.get("ready", [])],
            "message": _debrief_message(ss, rs), "build_block": build_block_message(st, step_id)}


def _debrief_message(ss: dict, rs: list[dict]) -> str:
    if ss["label"] == "not_started":
        return "Шаг не начат. Переход дальше открыт; вопросы шага останутся в рекомендациях — к ним можно вернуться."
    w = sum(1 for r in rs if r["level"] == "warning")
    c = len(rs) - w
    done = f"Сделано: {ss['closed']} из {ss['required']} вопросов."
    if not rs:
        return f"{done} Шаг закрыт полностью. Документ шага можно собирать."
    parts = []
    if w:
        parts.append(f"важно сделать до запуска — {w}")
    if c:
        parts.append(f"на развитие — {c}")
    tail = "Документ шага можно собрать — открытое уйдёт в него разделом рекомендаций." if ss["can_build"] else \
        "Документ шага соберётся, когда у каждого обязательного вопроса будет ответ, «Не знаю» или «Опускаю»."
    return f"{done} Рекомендации: {', '.join(parts)}. Дальше идти можно. {tail}"
