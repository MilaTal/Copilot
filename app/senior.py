"""Senior Pilot: проверка ответа, варианты решения, итог шага, разбор материалов разработки.

Вызовы идут через роутер. Если модель недоступна, ответ всё равно сохраняется — с пометкой «Senior Pilot не проверял»."""
from __future__ import annotations

import json
import re

from . import core, guard, mentor, readiness
from .router import Router, RouterError, extract_json
from .state import get_answer


def _value_text(q: dict, value) -> str:
    if q["type"] == "table":
        head = " | ".join(q["columns"])
        rows = "\n".join(" | ".join(r) for r in value or [])
        return f"{head}\n{rows}"
    if q["type"] == "multi":
        return "; ".join(value or [])
    return str(value)


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[а-яёa-z]{4,}", str(text or "").lower())}


def pick_knowledge(step_id: int, text: str, k: int = 3) -> list[dict]:
    """2–3 совета базы знаний наставника для шага: сначала те, у кого слова триггера («when») есть в ответе
    Junior Pilot; если совпадений мало — добираются первыми советами шага по порядку."""
    items = core.knowledge(step_id)
    if not items:
        return []
    words = _stems(text)
    scored = sorted(((len(_stems(a.get("when", "")) & words), i, a) for i, a in enumerate(items)),
                    key=lambda x: (-x[0], x[1]))
    hit = [a for score, _, a in scored if score > 0][:k]
    for a in items:
        if len(hit) >= 2:
            break
        if a not in hit:
            hit.append(a)
    return hit[:k]


# Источники, которые показываются у совета.
SHOWN_SOURCES = ("ТРИЗ", "теория управления проектами", "AI-native playbook", "исследование рынка аналогичных проектов и практика")


def source_mark(a: dict) -> str:
    """Пометка источника совета — только из разрешённого списка (несколько — через «;»), прочее отбрасывается."""
    parts = [p.strip() for p in str(a.get("source") or "").split(";")]
    shown = [p for p in parts if p in SHOWN_SOURCES]
    return f" ({'; '.join(shown)})" if shown else ""


def knowledge_block(step_id: int, text: str) -> str:
    """Опорные правила наставника для промпта: совет и вопрос, которым его открыть. Основа совета (basis) в промпт не
    идёт; пометка источника — только из SHOWN_SOURCES, и модель её сохраняет, если опирается на совет."""
    picked = pick_knowledge(step_id, text)
    if not picked:
        return ""
    lines = [f"- {a['advice'].strip()}{source_mark(a)} Открыть можно вопросом: {a['question'].strip()}" for a in picked]
    return ("Опорные правила наставника для этого шага (применяй, только если подходят к ответу; своими словами). "
            "Если у правила в скобках стоит пометка источника и ты на него опираешься — поставь эту пометку в скобках "
            "в конце своей фразы; других источников не называй:\n" + "\n".join(lines) + "\n")


def numbers_rule(step_id: int, qid: str | None) -> str:
    """Правило «без чисел» в отрезке шага (no_numbers_segment), если уровень шага сам по себе числа допускает."""
    if qid and core.no_numbers(step_id, qid) and core.level(step_id)["id"] != "sketch":
        seg = core.segments(step_id)[core.segment_index(step_id, qid) - 1]
        return (f"Отрезок «{seg['title']}» — без чисел и расчётов: не проси чисел, не давай образцов и примеров с "
                "числами; числа появятся в следующем отрезке шага.\n")
    return ""


def domain_block(st: dict, step_id: int, qid: str | None = None) -> str:
    """Обезличенные примеры отрасли проекта для этого шага — образцы уровня конкретики, а не готовые факты."""
    d = core.domain(st.get("domain"))
    ex = core.domain_examples(st.get("domain"), step_id)
    if not d or not ex or core.level(step_id)["id"] == "sketch" or core.no_numbers(step_id, qid):
        return ""  # на «наброске» и в отрезке «без чисел» — без чисел из чужих проектов
    return (f"Отрасль проекта: «{d['title']}». Обезличенные примеры ответов на этом шаге из похожих проектов "
            "(ориентир по конкретике; их числа — не факты этого проекта):\n" + "\n".join(f"- {x}" for x in ex) + "\n")


def project_context(st: dict, max_chars: int = 12000) -> str:
    """Сжатый контекст проекта: все данные ответы по шагам. Ни одного вызова модели."""
    lines = [f"Проект: {st['name']}."]
    d = core.domain(st.get("domain"))
    if d:
        lines.append(f"Отрасль: {d['title']}.")
    if st.get("description"):
        lines.append(f"Описание: {st['description']}")
    for s in core.steps():
        ans = (st.get("answers") or {}).get(str(s["id"])) or {}
        if not ans:
            continue
        lines.append(f"\n[{s['code']} {s['title']}]")
        for q in s["questions"]:
            rec = ans.get(q["id"])
            if not rec:
                continue
            label = q["text"].split("?")[0][:90]
            if rec["status"] == "answered" or rec["status"] == "needs_detail":
                extra = f" ({q.get('extra_text') or 'дополнительно'}: {rec['extra']})" if rec.get("extra") else ""
                lines.append(f"- {label}: {_value_text(q, rec['value'])[:1200]}{extra[:400]}")
            else:
                lines.append(f"- {label}: {core.STATUS_LABELS[rec['status']]}" + (f" ({rec['note']})" if rec.get("note") else ""))
    text = "\n".join(lines)[-max_chars:]
    inj = guard.detect_injection(text)
    if inj:
        guard.log("инъекция во вводе", ", ".join(inj), st.get("id", ""))
    return guard.wrap_data("Ответы проекта Junior Pilot", text)


def _system() -> str:
    return core.senior_rules()["persona"]


def _log_format_failure(purpose: str, r) -> None:
    """Сырой ответ модели при сбое формата — для разбора причины по факту, а не по догадке."""
    from datetime import datetime

    from . import config
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "purpose": purpose, "model": r.model,
           "truncated": r.truncated, "tokens_out": r.tokens_out, "text": r.text[:6000]}
    with open(config.data_dir() / "senior_format_failures.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def review_answer(st: dict, step_id: int, qid: str, router: Router | None = None) -> dict:
    q = core.question(step_id, qid)
    rec = get_answer(st, step_id, qid)
    s = core.step(step_id)
    rules = core.senior_rules()
    sg = mentor.stage(st, step_id, qid)
    if sg["stage"] >= 4:
        return mentor.defer_review(st, step_id, qid)  # три попытки были — не мучаем, откладываем с задачей
    answer_text = (_value_text(q, rec["value"])
                   + (f"\n{q.get('extra_text') or 'Дополнительно'}: {rec['extra']}" if rec.get("extra") else "")
                   + (f"\nКомментарий: {rec['note']}" if rec.get("note") else ""))
    seg = core.segments(step_id)[core.segment_index(step_id, qid) - 1]
    user = (f"Контекст проекта:\n{project_context(st)}\n\n"
            f"Шаг {s['code']} «{s['title']}»" + (f", отрезок «{seg['title']}»" if seg.get("title") else "")
            + f". Цель шага: {s['goal']}\n"
            f"Уровень шага: {core.level(step_id)['title']} — {core.level(step_id)['expect']}\n"
            + numbers_rule(step_id, qid)
            + f"Вопрос: {q['text']}\nЗачем он: {q['why']}\nКак ответить хорошо: {q['hint']}\n"
            + (f"Колонки таблицы: {', '.join(q['columns'])}. Минимум строк: {q.get('min_rows', 1)}\n" if q["type"] == "table" else "")
            + (f"Обязательные строки таблицы: {'; '.join(q['required_rows'])}\n" if q.get("required_rows") else "")
            + (f"Вопрос с вариантами. Все варианты: {'; '.join(q['options'])}. Пользователь выбрал: {rec['value']}. "
               "Выбор — факт: если он не из «рискованных», не возвращай ответ на доработку из-за самой формулировки "
               "вопроса и не предлагай считать выбор другим; замечания по существу давай отдельными рисками.\n"
               if q["type"] == "choice" and q.get("options") else "")
            + (f"Рядом с вариантом есть строка «{q['extra_text']}» — её тоже учитывай.\n" if q.get("extra_text") else "")
            + mentor.prompt_block(st, step_id, qid)
            + knowledge_block(step_id, answer_text)
            + "\n" + guard.wrap_data("Ответ Junior Pilot", answer_text) + "\n"
            + f"\n{rules['review_format']}")
    router = router or Router()
    data, cost = None, 0.0
    for max_tokens in (2500, 5000):  # ответ с образцом длиннее; при обрезке — один повтор с запасом
        r = router.chat("senior", _system(), [{"role": "user", "content": user}], max_tokens=max_tokens,
                        temperature=0.1, purpose=f"проверка ответа {s['code']}.{qid}")
        cost += r.cost_usd
        try:
            data = extract_json(r.text)
            break
        except (ValueError, json.JSONDecodeError):
            if not r.truncated:
                break
    if data is None:
        _log_format_failure(f"проверка ответа {s['code']}.{qid}", r)
        raise RouterError("Senior Pilot ответил не по формату" + (" — ответ обрезан пределом длины" if r.truncated else "")
                          + " — проверка не применена, ответ сохранён")
    if data.get("verdict") not in ("accepted", "needs_detail"):
        raise RouterError("Senior Pilot не вынес вердикт — проверка не применена, ответ сохранён")
    verdict = data["verdict"]
    data = guard.filter_obj(data)
    inj = guard.detect_injection(answer_text)
    if inj:
        data["risks"] = ["В ответе есть строки, похожие на команды для модели (" + ", ".join(inj)
                         + "). Они не выполнялись — уберите их из ответа."] + list(data.get("risks") or [])
    need = verdict == "needs_detail"
    opts = data.get("follow_options") if isinstance(data.get("follow_options"), list) else []
    return {"verdict": verdict, "comment": str(data.get("comment", ""))[:1200],
            "follow_up": str(data.get("follow_up", ""))[:900] if need else "",
            "follow_options": [str(x)[:200] for x in opts][:3] if need and sg["stage"] >= 2 else [],
            "sample": str(data.get("sample") or "")[:900] if need and sg["stage"] >= 3 and not q.get("no_sample") else "",
            "synthesis": str(data.get("synthesis") or "")[:900] if not need else "",
            "stage": sg["stage"], "short_mode": sg["short_mode"],
            "risks": [str(x)[:300] for x in (data.get("risks") or [])][:1],  # одна, самая полезная — без шума
            "model": r.model, "cost_usd": round(cost, 6)}


def propose_options(st: dict, step_id: int, qid: str, router: Router | None = None) -> dict:
    q = core.question(step_id, qid)
    s = core.step(step_id)
    rules = core.senior_rules()
    user = (f"Контекст проекта:\n{project_context(st)}\n\nШаг {s['code']} «{s['title']}».\n"
            + numbers_rule(step_id, qid)
            + f"Вопрос: {q['text']}\nЗачем: {q['why']}\nПодсказка: {q['hint']}\nПример из практики: {q.get('example', '')}\n"
            + domain_block(st, step_id, qid)
            + (f"Это таблица, колонки: {', '.join(q['columns'])}; в поле answer строки разделяй переводом строки, ячейки — символом «·».\n" if q["type"] == "table" else "")
            + (f"Варианты на выбор: {'; '.join(q.get('options', []))}\n" if q.get("options") else "")
            + f"\n{rules['options_format']}")
    router = router or Router()
    r = router.chat("senior", _system(), [{"role": "user", "content": user}], max_tokens=2500, temperature=0.4,
                    purpose=f"варианты {s['code']}.{qid}")
    try:
        data = extract_json(r.text)
        options = data.get("options") or []
    except (ValueError, json.JSONDecodeError):
        options = [{"title": "Ответ Senior Pilot", "answer": r.text.strip(), "pros": "", "cons": "", "when": ""}]
    return {"options": guard.filter_obj(options[:3]), "model": r.model, "cost_usd": r.cost_usd}


def debrief_comment(st: dict, step_id: int, router: Router | None = None) -> dict:
    d = readiness.step_debrief(st, step_id)
    s = core.step(step_id)
    risks = "\n".join(f"- [{r['level']}] {r['reason']}: {r['text']} (вопрос: {r['question'][:80]})" for r in d["risks"])
    user = (f"Контекст проекта:\n{project_context(st)}\n\nИтог шага {s['code']} «{s['title']}».\n"
            f"Критерии готовности шага:\n" + "\n".join(f"- {x}" for x in s.get("ready", []))
            + f"\nОткрытые пункты и риски:\n{risks or '- нет'}\n\n{core.senior_rules()['debrief_format']}")
    router = router or Router()
    r = router.chat("senior", _system(), [{"role": "user", "content": user}], max_tokens=900, temperature=0.2,
                    purpose=f"итог шага {s['code']}")
    return {"comment": guard.filter_output(r.text.strip())[0], "model": r.model, "cost_usd": r.cost_usd}


def ask(st: dict, step_id: int, message: str, router: Router | None = None) -> dict:
    s = core.step(step_id)
    о_совете = guard.is_advice_source_question(message)   # источник совета открыт, он показан у самого совета
    if not о_совете and guard.is_provenance_question(message):
        guard.log("вопрос о закрытой информации", message, st.get("id", ""))
        return {"answer": guard.CLOSED, "model": "guard", "cost_usd": 0}
    router = router or Router()
    if not о_совете and guard.classify_provenance(message, router):
        guard.log("вопрос о закрытой информации (классификатор)", message, st.get("id", ""))
        return {"answer": guard.CLOSED, "model": "guard", "cost_usd": 0}
    inj = guard.detect_injection(message)
    if inj:
        guard.log("инъекция в вопросе", ", ".join(inj), st.get("id", ""))
    user = (f"Контекст проекта:\n{project_context(st)}\n\nМы на шаге {s['code']} «{s['title']}». Цель: {s['goal']}\n"
            + domain_block(st, step_id)
            + knowledge_block(step_id, message)
            + guard.wrap_data("Вопрос Junior Pilot", message)
            + "\n\nОтветь по существу, коротко, с примером, если он помогает.")
    router = router or Router()
    r = router.chat("senior", _system(), [{"role": "user", "content": user}], max_tokens=1500, temperature=0.3,
                    purpose=f"вопрос на шаге {s['code']}")
    answer, leaked = guard.filter_output(r.text.strip())
    if leaked:
        guard.log("утечка в ответе вырезана", r.text[:200], st.get("id", ""))
    return {"answer": answer, "model": r.model, "cost_usd": r.cost_usd}


def review_material(st: dict, material: dict, router: Router | None = None) -> dict:
    """Разбор материалов разработки. Без флажка «можно в облако» роутер берёт только локальные точки."""
    findings = (st.get("workspace") or {}).get("last_scan", {}).get("findings", [])
    fl = "\n".join(f"- шаг {f['step']} [{f['level']}] {f['text']}" + (f" ({f['file']})" if f.get("file") else "")
                   for f in findings)
    user = (f"Ответы проекта:\n{project_context(st, 8000)}\n\nНаходки проверок кодом:\n{fl or '- нет'}\n\n"
            + guard.wrap_data("Материалы разработки Junior Pilot (секреты замаскированы)", material["text"])
            + f"\n\n{core.senior_rules()['review_material_format']}")
    router = router or Router()
    data, r = None, None
    for max_tokens in (6000, 12000):
        r = router.chat("reviewer", _system(), [{"role": "user", "content": user}], max_tokens=max_tokens,
                        temperature=0.1, purpose="разбор материалов разработки", only_local=not material["cloud_allowed"])
        try:
            data = extract_json(r.text)
            break
        except (ValueError, json.JSONDecodeError):
            if not r.truncated:
                break
    if data is None:
        raise RouterError("разбор пришёл не по формату" + (" — обрезан пределом длины" if r.truncated else "")
                          + "; повторите или сузьте материалы")
    data = guard.filter_obj(data)
    data["model"] = r.model
    data["endpoint"] = r.endpoint
    data["cost_usd"] = r.cost_usd
    return data


__all__ = ["review_answer", "propose_options", "debrief_comment", "ask", "review_material", "project_context", "RouterError"]
