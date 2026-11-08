"""Документы шагов и паспорт проекта — markdown из ответов, без вызова модели.

Документ шага собирается только когда у каждого обязательного вопроса есть ответ, «Не знаю» или «Опускаю».
Открытое не прячется: оно идёт в документ разделом «Открыто и риски»."""
from __future__ import annotations

from . import core, readiness
from .state import fmt_dt, get_answer, now, project_dir

STATUS_TEXT = {"unknown": "Не знаю", "skipped": "Сознательно опущено сейчас", "needs_detail": "Можно дополнить"}


def _cell(x: str) -> str:
    return str(x).replace("|", "/").replace("\n", " ")


def render_answer(q: dict, rec: dict | None) -> list[str]:
    if not rec:
        return ["_Нет ответа._"]
    if rec["status"] in ("unknown", "skipped"):
        out = [f"_{STATUS_TEXT[rec['status']]}._ Рекомендация: {q['risk']['text']}"]
        if rec.get("note"):
            out.append(f"Комментарий: {rec['note']}")
        return out
    v = rec["value"]
    out = []
    if q["type"] == "table":
        out.append("| " + " | ".join(_cell(c) for c in q["columns"]) + " |")
        out.append("|" + "---|" * len(q["columns"]))
        for row in v:
            out.append("| " + " | ".join(_cell(c) for c in row) + " |")
    elif q["type"] == "multi":
        out += [f"- {x}" for x in v]
    else:
        out.append(str(v))
    if rec.get("extra"):
        out.append(f"{q.get('extra_text') or 'Дополнительно'}: {rec['extra']}")
    if rec["status"] == "needs_detail":
        follow = (rec.get("senior") or {}).get("follow_up")
        out.append(f"_Подсказка Senior Pilot: {follow or 'дополните ответ деталями'}._")
    if (rec.get("synthesis") or {}).get("text"):
        out.append(f"_Записано: {rec['synthesis']['text']}_")
    if rec.get("note"):
        out.append(f"Комментарий: {rec['note']}")
    return out


def step_markdown(st: dict, step_id: int) -> str:
    s = core.step(step_id)
    ss = readiness.step_status(st, s)
    lines = [f"# {s['code']} {s['title']} — {s['artifact']}", "",
             f"Проект: {st['name']}. Собрано: {fmt_dt(now())}.", "", f"Цель шага: {s['goal']}", ""]
    syn = (st.get("synthesis") or {}).get(str(step_id))
    if syn:  # подтверждённый вывод шага — главное в документе, поэтому первым
        title = (s.get("synthesis") or {}).get("title") or "Вывод шага"
        mark = ("_Перенесено с прежнего маршрута — нужно подтвердить заново._" if syn.get("needs_reconfirm")
                else f"_Подтверждено {fmt_dt(syn['confirmed'])}._")
        lines += [f"## {title}", "", syn["text"], "", mark, ""]
    for q in s["questions"]:
        lines += [f"## {q['text']}", ""] + render_answer(q, get_answer(st, step_id, q["id"])) + [""]
    lines += ["## Готовность шага", ""]
    lines.append(f"Закрыто обязательных вопросов: {ss['closed']} из {ss['required']}; полных ответов: {ss['full']}.")
    lines += [""] + [f"- {x}" for x in s.get("ready", [])] + [""]
    rs = [r for r in readiness.risks(st) if r["step"] == int(step_id)]
    lines += ["## Рекомендации для развития", ""]
    if rs:
        lines += ["| Важность | Причина | Рекомендация | Кто займётся |", "|---|---|---|---|"]
        for r in rs:
            lvl = "важно до запуска" if r["level"] == "warning" else "на развитие"
            lines.append(f"| {lvl} | {r['reason']} | {_cell(r['text'])} | {_cell(r['owner'] or 'не назначен')} |")
    else:
        lines.append("Открытых рекомендаций нет.")
    return "\n".join(lines) + "\n"


def build_step(st: dict, step_id: int) -> dict:
    s = core.step(step_id)
    ss = readiness.step_status(st, s)
    if not ss["can_build"]:
        # до 12.10.2026 перечислялись только вопросы без ответа: при «нужно уточнить» список был пуст и человек
        # не видел, что держит сборку. Теперь — каждая причина словами и что с ней сделать
        raise ValueError(readiness.build_block_message(st, step_id))
    md = step_markdown(st, step_id)
    d = project_dir(st["id"]) / "docs"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{s['code']} {s['artifact'].replace('/', '-').replace('«', '').replace('»', '')}.md"
    path.write_text(md, encoding="utf-8")
    st.setdefault("artifacts", {})[str(step_id)] = {"built": now(), "path": path.name, "stale": False}
    return {"path": path.name, "markdown": md}


def passport(st: dict) -> dict:
    ov = readiness.overall(st)
    lines = [f"# Паспорт проекта — {st['name']}", "",
             f"Собрано: {fmt_dt(now())}. Готовность к проду: {ov['prod_readiness_pct']} %. "
             f"Критичных рисков: {ov['warnings']}, требующих внимания: {ov['cautions']}.", ""]
    for p in core.phases():
        lines += [f"# {p['title']}", ""]
        for sid in p["steps"]:
            body = step_markdown(st, sid).split("\n")
            body[0] = body[0].replace("# ", "## ", 1)
            body = [("#" + l) if l.startswith("## ") and i > 0 else l for i, l in enumerate(body)]
            lines += body + [""]
    from .recommend import after_launch_lines
    after = after_launch_lines(st)
    if after:
        lines += ["# План после запуска", ""] + [f"- {x}" for x in after] + [""]
    md = "\n".join(lines)
    d = project_dir(st["id"]) / "docs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "Паспорт проекта.md").write_text(md, encoding="utf-8")
    return {"path": "Паспорт проекта.md", "markdown": md}


def list_docs(st: dict) -> list[dict]:
    d = project_dir(st["id"]) / "docs"
    if not d.exists():
        return []
    return [{"name": f.name, "size": f.stat().st_size} for f in sorted(d.glob("*.md"))]


def read_doc(st: dict, name: str) -> str:
    d = (project_dir(st["id"]) / "docs").resolve()
    f = (d / name).resolve()
    if d not in f.parents or not f.exists():
        raise FileNotFoundError("документ не найден")
    return f.read_text(encoding="utf-8")
