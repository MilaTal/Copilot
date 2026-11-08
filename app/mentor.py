"""Модель диалога Senior Pilot: лестница поддержки, бюджет наводящих, утомление, синтез ответа и шага, предзаполнение.

Лестница по одному вопросу (ступень растёт с каждым «нужно уточнить»):
  1 — наводящий открытый вопрос, без образца;
  2 — наводящий с 2–3 вариантами из ответов проекта;
  3 — образец с пустыми местами «[…]» (для решений Junior Pilot — варианты с плюсами и минусами, без образца);
  4 — отложить с задачей: у кого узнать и первый шаг. Дальше вопрос не мучаем.
На ступень ниже в том же вопросе не возвращаемся. Хорошее отмечается кивком, похвала — только за сложное.
Бюджет наводящих считается на отрезок шага и растёт с числом его вопросов: доля BUDGET_RATE от числа вопросов
отрезка, но не меньше BUDGET_MIN. Исчерпан бюджет отрезка или видно утомление внутри отрезка — короткий режим:
сразу ступень 3."""
from __future__ import annotations

import math
import re

from . import core
from .state import get_answer, now

# доля вопросов отрезка, на которые хватит наводящего: на наброске и проекте меньше, на конструкции и расчёте больше
BUDGET_RATE = {"sketch": 0.6, "project": 0.6, "design": 0.9, "calc": 0.9}
BUDGET_MIN = 3


def budget(step_id: int, qid: str) -> int:
    """Бюджет наводящих вопросов на отрезок, в котором стоит вопрос."""
    lvl = core.level(step_id)["id"]
    seg = core.segments(step_id)[core.segment_index(step_id, qid) - 1]
    return max(BUDGET_MIN, math.ceil(len(seg["questions"]) * BUDGET_RATE.get(lvl, 0.6)))
STAGE_TEXT = {
    1: "Ступень 1 — если ответа не хватает, задай ОДИН наводящий открытый вопрос, который сужает поле, но не содержит "
       "ответа. Образец не давай.",
    2: "Ступень 2 — Junior Pilot уже отвечал на наводящий вопрос. Если всё ещё не хватает — дай наводящий вопрос с 2–3 "
       "вариантами, взятыми из ответов проекта и его отрасли, плюс «свой вариант». Варианты — в поле follow_options.",
    3: "Ступень 3 — дай образец с пустыми местами «[…]», собранный из слов самого Junior Pilot. Он заполнит и поправит.",
}
NO_SAMPLE_TEXT = ("Этот вопрос — решение Junior Pilot и заказчика: образец не давай, вместо него 2–3 варианта с плюсами, "
                  "минусами и условием выбора в поле follow_options.")


def _key(step_id: int, qid: str) -> str:
    return f"{int(step_id)}:{qid}"


def _words(rec: dict | None) -> int:
    if not rec:
        return 0
    v = rec.get("value")
    text = " ".join(" ".join(r) if isinstance(r, list) else str(r) for r in v) if isinstance(v, list) else str(v or "")
    return len(re.findall(r"\w+", text))


def guided(st: dict, step_id: int, qid: str) -> int:
    return int(((st.get("mentor") or {}).get(_key(step_id, qid)) or {}).get("guided", 0))


def _segment_qids(step_id: int, qid: str | None) -> set[str] | None:
    if qid is None:
        return None
    return set(core.segments(step_id)[core.segment_index(step_id, qid) - 1]["questions"])


def step_guided(st: dict, step_id: int, qid: str | None = None) -> int:
    """Наводящие вопросы шага; с qid — только внутри его отрезка. Ключи вне ядра (старые, чужие) тоже считаются."""
    seg = _segment_qids(step_id, qid)
    total = 0
    for k, v in (st.get("mentor") or {}).items():
        s, _, q = k.partition(":")
        if s != str(int(step_id)):
            continue
        if seg is not None and q in core_qids(step_id) and q not in seg:
            continue
        total += int((v or {}).get("guided", 0))
    return total


def core_qids(step_id: int) -> set[str]:
    return {q["id"] for q in core.step(step_id)["questions"]}


def tired(st: dict, step_id: int, qid: str | None = None) -> bool:
    """Два последних ответа отрезка короче пяти слов или два «не знаю» подряд — признак утомления, а не незнания.
    Без qid — по всему шагу."""
    seg = _segment_qids(step_id, qid)
    recs = [r for k, r in ((st.get("answers") or {}).get(str(step_id)) or {}).items() if r and (seg is None or k in seg)]
    recs.sort(key=lambda r: r.get("updated") or "")
    last = recs[-2:]
    if len(last) < 2:
        return False
    return all(r["status"] == "unknown" for r in last) or all(r["status"] == "answered" and _words(r) < 5 for r in last)


def stage(st: dict, step_id: int, qid: str) -> dict:
    lvl = core.level(step_id)["id"]
    n = guided(st, step_id, qid)
    left = budget(step_id, qid) - step_guided(st, step_id, qid)
    short = left <= 0 or tired(st, step_id, qid)
    if n >= 3:
        s = 4
    elif short:
        s = 3
    else:
        s = n + 1
    return {"stage": s, "short_mode": short, "budget_left": max(left, 0), "level": lvl}


def prompt_block(st: dict, step_id: int, qid: str) -> str:
    """Что сказать модели о ходе разговора по этому вопросу."""
    q = core.question(step_id, qid)
    sg = stage(st, step_id, qid)
    lines = [f"Ход разговора: {STAGE_TEXT.get(min(sg['stage'], 3))}"]
    if sg["short_mode"]:
        lines.append("Короткий режим: Junior Pilot устал или бюджет наводящих вопросов шага исчерпан — без долгих "
                     "объяснений, сразу образец или варианты.")
    if q.get("no_sample"):
        lines.append(NO_SAMPLE_TEXT)
    if q.get("guide"):
        lines.append("Наводящие вопросы ядра (бери по смыслу, своими словами): " + " · ".join(q["guide"]))
    if q.get("mentor_note"):
        lines.append(f"Зачем это в проде (объясни один раз, если ответ неполный): {q['mentor_note']}")
    return "\n".join(lines) + "\n"


def defer_review(st: dict, step_id: int, qid: str) -> dict:
    """Ступень 4 — без вызова модели: вопрос остаётся открытым с задачей, Junior Pilot идёт дальше."""
    q = core.question(step_id, qid)
    hint = (q.get("guide") or [q.get("hint", "")])[0]
    return {"verdict": "accepted", "comment": "Записано как есть. Дальше идти можно.",
            "follow_up": "", "follow_options": [], "sample": "", "synthesis": "",
            "risks": [f"Вернуться к вопросу, когда появятся данные. Первый шаг: {hint}"[:300]],
            "stage": 4, "model": "mentor", "cost_usd": 0}


def record(st: dict, step_id: int, qid: str, review: dict) -> None:
    """Счётчик наводящих: каждое «нужно уточнить» поднимает ступень."""
    m = st.setdefault("mentor", {}).setdefault(_key(step_id, qid), {"guided": 0})
    if review.get("verdict") == "needs_detail":
        m["guided"] = int(m.get("guided", 0)) + 1
    m["updated"] = now()


def confirm_answer_synthesis(st: dict, step_id: int, qid: str, text: str) -> dict:
    """Х5: Junior Pilot подтверждает или правит формулировку «Записываю так: …»."""
    rec = get_answer(st, step_id, qid)
    if not rec:
        raise ValueError("сначала ответьте на вопрос")
    text = str(text or "").strip()[:1500]
    if not text:
        raise ValueError("пустая формулировка")
    rec["synthesis"] = {"text": text, "confirmed": now()}
    return rec["synthesis"]


_PH = re.compile(r"\[([a-z_]+)(?::\s*([^\]]*))?\]")


def _answer_short(st: dict, step_id: int, qid: str) -> str | None:
    try:
        sid = core.qstep(qid)
    except KeyError:
        return None
    q = core.question(sid, qid)
    rec = get_answer(st, sid, qid)
    if not rec:
        return None
    if (rec.get("synthesis") or {}).get("text"):
        return rec["synthesis"]["text"]
    if rec["status"] != "answered":
        return None
    v = rec["value"]
    if q["type"] == "table":
        return f"{len(v)} строк(и)"
    if isinstance(v, list):
        return "; ".join(v)
    out = str(v).strip().rstrip(".")[:400]
    if rec.get("extra"):
        out += f"; {rec['extra'][:200]}"
    return out


def step_synthesis_draft(st: dict, step_id: int) -> dict:
    """Вывод шага по шаблону ядра — собирает код из ответов, без модели. Пустые места остаются видны."""
    s = core.step(step_id)
    syn = s.get("synthesis") or {}
    tpl = syn.get("template", "")
    missing = []

    def fill(m):
        qid, note = m.group(1), m.group(2)
        val = _answer_short(st, step_id, qid)
        if val:
            return val
        missing.append(qid)
        return f"[{note or qid}: ещё нет ответа]"
    text = _PH.sub(fill, tpl) if tpl else ""
    saved = (st.get("synthesis") or {}).get(str(step_id))
    return {"title": syn.get("title", ""), "draft": text, "missing": missing, "saved": saved,
            "lead": s.get("lead", ""), "gate": s.get("gate")}


def save_step_synthesis(st: dict, step_id: int, text: str) -> dict:
    text = str(text or "").strip()[:4000]
    if not text:
        raise ValueError("пустой вывод шага")
    st.setdefault("synthesis", {})[str(step_id)] = {"text": text, "confirmed": now()}
    art = st.get("artifacts", {}).get(str(step_id))
    if art:
        art["stale"] = True
    return st["synthesis"][str(step_id)]


def prefill(st: dict, step_id: int, qid: str) -> list[list[str]]:
    """Черновик таблицы: обязательные строки ядра (required_rows) сверху, дальше — из уже данных ответов
    (prefill_from), чтобы не спрашивать одно и то же дважды."""
    from .state import required_rows_template
    q = core.question(step_id, qid)
    if q["type"] != "table":
        return []
    cols = len(q["columns"])
    rows: list[list[str]] = list(required_rows_template(q))
    for src in q.get("prefill_from") or []:
        try:
            sid = core.qstep(src)
        except KeyError:
            continue
        sq = core.question(sid, src)
        rec = get_answer(st, sid, src)
        if not rec or rec["status"] != "answered":
            continue
        if sq["type"] == "table":
            for r in rec["value"]:
                rows.append((list(r) + [""] * cols)[:cols])
        else:
            v = "; ".join(rec["value"]) if isinstance(rec["value"], list) else str(rec["value"])
            rows.append(([v[:300]] + [""] * cols)[:cols])
    seen, out = set(), []
    for r in rows:
        k = tuple(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out[:30]


def prefill_text(st: dict, step_id: int, qid: str) -> str:
    """Черновик текстового ответа из карточки выбранного паттерна (поле ядра prefill_pattern):
    кто решает, где предел, что после предела. Паттерн не выбран — пустая строка."""
    q = core.question(step_id, qid)
    if not q.get("prefill_pattern"):
        return ""
    try:
        psid = core.qstep("pattern")
    except KeyError:
        return ""
    rec = get_answer(st, psid, "pattern")
    if not rec or rec["status"] not in ("answered", "needs_detail") or not rec.get("value"):
        return ""
    pid = (core._load("patterns.yaml").get("choice_map") or {}).get(rec["value"])
    card = next((p for p in core.patterns() if p["id"] == pid), None)
    if not card:
        return ""
    return (f"Кто решает: {card.get('who_decides', '')}\nГде предел: {card.get('limit', '')}\n"
            f"Что после предела: {card.get('after', '')}")


# Переезд сохранённых проектов — в модуле migration; имена оставлены для прежних вызовов.
from .migration import SCHEMA, migrate  # noqa: E402,F401
