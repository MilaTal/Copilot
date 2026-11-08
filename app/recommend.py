"""Рекомендательная система Senior Pilot.

Что и с каким приоритетом рекомендовать — решает код (бесплатно и воспроизводимо). Senior Pilot по кнопке
переводит рекомендацию на язык проекта: план из нескольких действий и образец.

Рекомендация: что сделать · зачем · как (план) · образец · шаг и вопрос · польза · трудоёмкость · статус ·
вид (kind: advance — двигает дальше, rework — доработать). Тупиков нет (12.10.2026): навигация по маршруту
(navigation) всегда в «Следующем шаге», а если всё отложено или отклонено — показывается страховкой.
Статусы: new → in_progress → done; deferred (вернётся на нужном шаге); after_launch (на последнем шаге — план после
запуска, с владельцем и стадией); dismissed (не актуально, с причиной). У каждой рекомендации — путь по шагам в
copilot (path): где делать, что сделать, как понять, что готово.
«Сделано» перепроверяется: если условие, из-за которого появилась рекомендация, ушло — подтверждено и похвала;
если нет — рекомендация остаётся в работе с подсказкой, что ещё поменять. Исправление ответа закрывает
рекомендацию само — и это тоже повод похвалить."""
from __future__ import annotations

import hashlib
import json

from . import core, guard, readiness
from .state import get_answer, now, project_dir

IMPACT = {"warning": 3, "caution": 2, "senior": 1, "domain": 1, "life": 2}
TITLE = {
    "Нет ответа": "Ответьте на вопрос",
    "Ответ «Не знаю»": "Выясните ответ",
    "Сознательно опущено": "Вернитесь к вопросу",
    "Senior Pilot подсказывает, что добавить": "Дополните ответ",
    "Мало строк": "Добавьте строки в таблицу",
    "Нет обязательной строки": "Добавьте обязательную строку в таблицу",
    "Выбран рискованный вариант": "Закройте риск выбранного варианта",
    "Не проверено на деле": "Проверьте на деле",
    "Расхождение между шагами": "Сверьте шаги между собой",
    "Находка испытаний": "Усилите по итогам испытаний",
    "Находка проверки": "Доработайте по итогам проверки качества",
    "Находка в разработке": "Доработайте разработку",
    "Замечание Senior Pilot": "Совет Senior Pilot",
    "Добавлено вручную": "Рекомендация",
}
# Кивок — коротко и по-дружески; развёрнутая похвала — только за сложное или нестандартное
NODS = ["Принято.", "Готово.", "Закрыто.", "Есть."]
PRAISE_HARD = ["Сильное решение — это было непросто.", "Хорошо разобрались: такое часто всплывает только в проде.",
               "Нестандартно и точно — это укрепило проект."]


def _ack(rec: dict, rid: str) -> str:
    hard = rec.get("effort", 1) >= 3 or rec.get("source") in ("consistency", "attack", "eval", "workspace")
    pool = PRAISE_HARD if hard else NODS
    return pool[int(hashlib.sha1(rid.encode()).hexdigest(), 16) % len(pool)]
SELF_CHECKED = {"senior", "domain", "manual"}  # условие не проверяется кодом — отметку Junior Pilot принимаем


def _rid(*parts) -> str:
    return "r-" + hashlib.sha1("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:10]


def _effort(src: str, q: dict | None) -> int:
    if src in ("attack", "eval", "workspace"):
        return 3
    if src == "consistency" or (q and q["type"] == "table"):
        return 2
    return 1


def _plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    return one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many


def _short(text: str, limit: int = 90) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _reached(st: dict, cur: int) -> int:
    """Самый дальний шаг, до которого проект доходил: текущий, шаг старта или любой начатый. Жизненный цикл
    (прототип, защита, опыт отрасли) считается от него — возврат назад к прошлому шагу их не снимает."""
    touched = [int(k) for k, v in (st.get("answers") or {}).items() if v]
    return max([cur, int(st.get("start_step") or 1)] + touched)


def _defense_built(st: dict) -> bool:
    try:
        return (project_dir(st["id"]) / "docs" / "Каркас защиты.md").exists()
    except (KeyError, TypeError, ValueError):
        return False


def _blocker_lines(st: dict, step_id: int, limit: int = 3) -> list[str]:
    out = []
    for b in readiness.build_blockers(st, step_id)[:limit]:
        q = f"«{_short(b['text'])}»"
        if b["reason"] == "needs_detail":
            out.append(f"Нужно уточнить {q}" + (f": {_short(b['follow_up'], 200).rstrip('.')}." if b["follow_up"] else "."))
        else:
            out.append(f"Нет ответа: {q}.")
    return out


NO_DATA_HOW = "Нет данных — отметьте «Не знаю» или «Опускаю» с причиной: вопрос закроется и уйдёт в рекомендации."


def _life(rid: str, step: int, title: str, why: str, how: list[str], kind: str, impact: int = IMPACT["life"],
          level: str = "caution", focus: str | None = None, nav: str | None = None) -> dict:
    """Рекомендация жизненного цикла. qid у неё пуст: это не рекомендация по одному вопросу; первый открытый вопрос,
    к которому она ведёт, — в focus_qid."""
    rec = {"id": rid, "source": "life", "risk_id": None, "step": step, "code": core.code(step), "qid": None,
           "title": title[:300], "why": why, "how": how, "example": "", "impact": impact, "effort": 1,
           "level": level, "kind": kind}
    if focus:
        rec["focus_qid"] = focus
    if nav:
        rec["nav"] = nav
    return rec


def _defense_rec() -> dict:
    last = core.step_ids()[-1]
    return _life(_rid("defense"), last, "Соберите каркас защиты и проверьте сверку чисел",
                 "ЛПР финансирует защищённый проект: 3–4 слайда, у каждого числа — источник.",
                 ["Откройте «Защита» и нажмите «Собрать каркас по проекту».", "Закройте пустые места."], "advance")


def navigation(st: dict, ss: dict | None = None) -> list[dict]:
    """Навигация по маршруту — чтобы у Junior Pilot всегда был следующий ход.

    primary — одна на проект: текущий шаг не закрыт → «Ответьте на открытые вопросы» (rework); закрыт → «Перейдите
    к шагу» — ближайший незакрытый дальше по маршруту, а если дальше всё закрыто — ближайший незакрытый раньше
    (advance); закрыто всё → каркас защиты, а после него — «Проект готов к защите» (advance).
    return — по одной на каждый незакрытый шаг раньше текущего: «Вернитесь к шагу» (rework)."""
    ss = ss or {x["step"]: x for x in readiness.all_status(st)}
    ids = core.step_ids()
    cur = int(st.get("current_step") or 1)
    x = ss[cur]
    out = []
    if not x["can_build"]:
        bl = readiness.build_blockers(st, cur)
        nd = [b for b in bl if b["reason"] == "needs_detail"]
        why = ("Документ шага соберётся, когда у каждого обязательного вопроса будет ответ, «Не знаю» или «Опускаю»."
               + (f" Ждут уточнения после проверки Senior Pilot: {len(nd)}." if nd else ""))
        out.append(_life(_rid("open", cur), cur,
                         f"Ответьте на открытые вопросы шага {x['code']} (открыто {len(bl)} из {x['required']})",
                         why, _blocker_lines(st, cur) + [NO_DATA_HOW], "rework", impact=3,
                         focus=bl[0]["qid"] if bl else None, nav="primary"))
    else:
        later = [s for s in ids if s > cur and not ss[s]["can_build"]]
        earlier = [s for s in ids if s < cur and not ss[s]["can_build"]]
        if later or earlier:
            t = later[0] if later else earlier[-1]  # назад — ближайший к текущему: идут Ш6 → Ш5 → …
            first_q = (readiness.build_blockers(st, t) or [{"text": ""}])[0]["text"]
            skipped = [ss[s]["code"] for s in ids if cur < s < t]
            why = (f"Шаг {x['code']} закрыт: на каждый обязательный вопрос есть ответ, «Не знаю» или «Опускаю»; "
                   "открытое уйдёт в документ шага разделом рекомендаций.")
            if skipped:
                why += f" Шаги {', '.join(skipped)} уже закрыты."
            if not later:
                why += f" Дальше по маршруту всё закрыто — ближайший незакрытый шаг {ss[t]['code']}."
            how = ([f"В итоге шага {x['code']} нажмите «Следующая точка»."] if t == cur + 1 else
                   [f"Откройте шаг {ss[t]['code']} на маршруте."])
            if first_q:
                how.append(f"Начните с вопроса «{_short(first_q)}».")
            out.append(_life(_rid("advance", t), cur, f"Перейдите к шагу {ss[t]['code']} «{ss[t]['title']}»", why, how,
                             "advance", impact=3, nav="primary"))
        elif _defense_built(st):
            n = len(ids)
            out.append(_life(_rid("finish"), ids[-1],
                             f"Проект готов к защите: все {n} шагов закрыты, каркас защиты собран",
                             "Осталось выступление перед ЛПР: 3–4 слайда, у каждого числа — источник, на пять вопросов "
                             "ЛПР — ответ числом.",
                             ["Откройте «Защита» и проверьте решение «взлёт / не взлёт» и сверку чисел.",
                              "Соберите паспорт проекта — в нём документы всех шагов.",
                              "Проговорите пять вопросов ЛПР вслух: на каждый — ответ числом."],
                             "advance", impact=3, nav="primary"))
        else:
            out.append({**_defense_rec(), "impact": 3, "nav": "primary"})
    for k in ids:
        if k >= cur or ss[k]["can_build"]:
            continue
        n = ss[k]["required"] - ss[k]["closed"]
        out.append(_life(_rid("return", k), k,
                         f"Вернитесь к шагу {ss[k]['code']} «{ss[k]['title']}»: не закрыто {n} "
                         f"{_plural(n, 'вопрос', 'вопроса', 'вопросов')} из {ss[k]['required']}",
                         f"Шаг {ss[k]['code']} раньше текущего остался незакрытым — проект начат с середины или шаги идут "
                         "не по порядку. Пока он открыт, его документ и паспорт проекта не соберутся.",
                         _blocker_lines(st, k, 2) + [NO_DATA_HOW], "rework",
                         focus=(readiness.build_blockers(st, k) or [{"qid": None}])[0]["qid"], nav="return"))
    return out


def candidates(st: dict) -> dict[str, dict]:
    """Все рекомендации, которые сейчас актуальны, из правил ядра. Ни одного вызова модели.

    kind у каждой: advance — двигает дальше (перейти к шагу, собрать документ, прототип, защиту); rework — доработать
    (ответ, риск, вернуться к шагу). Опыт отрасли — rework: подсказка проверяет и усиливает уже данные ответы, а не
    открывает новый этап."""
    out: dict[str, dict] = {}
    ss = {x["step"]: x for x in readiness.all_status(st)}
    for r in navigation(st, ss):  # первыми — при равной оценке навигация стоит выше
        out[r["id"]] = r
    for r in readiness.risks(st):
        q = core.question(r["step"], r["qid"]) if r.get("qid") else None
        src = r["source"] if r["source"] != "вопрос" else "question"
        rid = _rid(r["id"])
        how, example = [], ""
        if q:
            how.append(q["hint"])
            example = q.get("example", "")
            rec = get_answer(st, r["step"], r["qid"])
            follow = ((rec or {}).get("senior") or {}).get("follow_up")
            if follow and follow.strip() != str(q["hint"]).strip():  # подсказка ядра и Senior Pilot бывают одной строкой
                how.insert(0, follow)
        title = TITLE.get(r["reason"], "Рекомендация")
        what = f"{title}: {r['question'] or r['text']}" if q else f"{title}: {r['text']}"
        out[rid] = {"id": rid, "source": src, "risk_id": r["id"], "step": r["step"], "code": r["code"],
                    "qid": r.get("qid"), "title": what[:300], "why": r["text"] if q else _why_for(src),
                    "how": how, "example": example,
                    "impact": IMPACT["senior"] if src == "senior" else IMPACT.get(r["level"], 2),
                    "effort": _effort(src, q), "level": r["level"], "kind": "rework"}
    # жизненный цикл проекта: документы, прототип, проверки, защита
    cur = int(st.get("current_step") or 1)
    reached = _reached(st, cur)  # до 12.10.2026 считалось от текущего шага: возврат назад «закрывал» прототип и защиту с похвалой
    for sid, x in ss.items():
        if x["can_build"] and (not x["artifact"] or x["artifact_stale"]):
            rid = _rid("doc", sid)
            out[rid] = _life(rid, sid, f"Соберите документ шага {x['code']} «{x['title']}»",
                             "Документ шага — то, что останется у заказчика и пойдёт в паспорт проекта.",
                             ["Нажмите «Собрать документ шага» в итоге шага."], "advance")
    ps = core.qstep("prototype_run")
    proto = get_answer(st, ps, "prototype_run") or {}
    if reached >= ps and not str(proto.get("value") or "").startswith("Да"):
        rid = _rid("proto")
        out.setdefault(rid, {**_life(rid, ps, "Соберите прототип и прогоните его хотя бы раз",
                                     "Пока действие не видно во внешней системе, архитектура не проверена реальностью.",
                                     [f"На шаге {core.code(ps)} нажмите «Собрать прототип».", "Поднимите каркас агента на стенде.",
                                      "Прогоните один реальный вход и проверьте результат во второй вкладке."],
                                     "advance", impact=3, level="warning"), "effort": 2})
    last = core.step_ids()[-1]
    if reached >= last and not _defense_built(st):
        out.setdefault(_rid("defense"), _defense_rec())
    # опыт отрасли — по одной подсказке на шаг, не дальше следующего шага
    d = core.domain(st.get("domain"))
    for h in (d or {}).get("hints", []):
        if h["step"] <= reached + 1:
            rid = _rid("domain", h["title"])
            out[rid] = {"id": rid, "source": "domain", "risk_id": None, "step": h["step"], "code": core.code(h["step"]),
                        "qid": None, "title": f"Учтите опыт отрасли: {h['title']}", "why": h["text"],
                        "how": [h["lesson"]], "example": "", "impact": IMPACT["domain"], "effort": 1, "level": "caution",
                        "kind": "rework"}
    return out


# ---------- путь по шагам в copilot ----------
STAGES = ("тень", "человек в контуре", "самостоятельно")
DONE_AUTO = "Готово, когда Copilot сам увидит изменение: рекомендация закроется и уйдёт в «Закрыто» с отметкой."
DONE_SELF = "Готово — в меню «⋯» рекомендации нажмите «Сделано»: эту отметку Copilot принимает с ваших слов."
DONE_NAV = "Готово, когда шаг закрыт: вместо этой рекомендации появится следующий ход по маршруту."


def _where(rec: dict) -> str:
    src = rec.get("source")
    if src == "workspace":
        return "Откройте экран «Разработка» в верхнем меню — там находка проверки кода."
    try:
        title = core.step(rec["step"])["title"]
    except (KeyError, ValueError, TypeError):
        title = ""
    place = f"Откройте шаг {rec['code']} «{title}» на маршруте"
    if src == "attack":
        return place + " → блок «Испытания — запуск двумя ключами»."
    if src == "eval":
        return place + " → блок «Стоимость успешного результата и прогон набора»."
    qid = rec.get("qid") or rec.get("focus_qid")
    if qid:
        try:
            return place + f" → вопрос «{_short(core.question(rec['step'], qid)['text'], 70)}»."
        except (KeyError, ValueError):
            pass
    return place + "."


def path(rec: dict, meta: dict | None = None, last: bool = False) -> list[str]:
    """Путь по шагам в copilot: где это делается, что сделать, как понять, что готово. Для отложенного — когда
    вернётся; для плана после запуска — стадия и владелец; для «важно до запуска» на последнем шаге — как принять
    риском, раз отложить его нельзя."""
    meta = meta or {}
    how = [h for h in (rec.get("how") or []) if h]
    if last and rec.get("level") == "warning" and not rec.get("nav"):
        how = how + [f"Закрыть до запуска не выходит — примите риском: на шаге {core.code(core.qstep('risk_table'))} "
                     "в вопросе «Реестр рисков» добавьте строку «действие · риск · контроль · владелец (роль)», затем в "
                     "меню «⋯» — «Не актуально» с причиной «принято риском, строка в реестре рисков»."]
    head = [] if any(h.startswith(("Откройте", "На шаге", "В итоге шага")) for h in how[:1]) else [_where(rec)]
    if rec.get("nav") == "primary" and rec.get("kind") == "rework":
        done = DONE_NAV
    elif rec.get("source") in SELF_CHECKED:
        done = DONE_SELF
    else:
        done = DONE_AUTO
    status = meta.get("status")
    if status == "deferred" and meta.get("defer_step"):
        head.insert(0, f"Отложено до шага {core.code(meta['defer_step'])}: вернётся в «Следующий шаг», когда вы его "
                       "откроете. Закрыть раньше можно так:")
    elif status == "after_launch":
        head = [f"В плане после запуска: закрыть до перехода на стадию «{meta.get('stage') or '—'}», "
                f"владелец — {meta.get('owner') or '[роль не указана]'}.", "Перед этим переходом владелец открывает "
                "«План развития» → раздел «После запуска» и делает по шагам:"] + head
        done = "Готово — в «План развития» → «После запуска» → меню «⋯» нажмите «Сделано»."
    return head + how + [done]


def _why_for(src: str) -> str:
    return {"consistency": "Ответы разных шагов расходятся — на защите это заметят первым.",
            "attack": "Испытание показало место, которое надо укрепить до запуска.",
            "eval": "Проверка качества нашла расхождение с ожидаемым результатом.",
            "workspace": "Проверка кода нашла, что механизм заявлен, но в разработке его нет.",
            "senior": "Совет наставника по вашему ответу.",
            "прогон": "Ответы описывают намерение, а проверка на деле ещё не проводилась."}.get(src, "")


def _score(rec: dict, cur: int) -> float:
    # польза × срочность ÷ трудоёмкость; срочность — насколько шаг рекомендации близок к текущему или уже пройден
    gap = rec["step"] - cur
    urgency = 1.0 if gap <= 0 else (0.7 if gap == 1 else 0.4)
    return rec["impact"] * urgency / rec["effort"]


def view(st: dict) -> dict:
    """Следующий шаг (топ-3) и полный план развития со статусами. Заодно закрывает сами собой ушедшие рекомендации."""
    cand = candidates(st)
    saved: dict = st.setdefault("recs", {})
    cur = int(st.get("current_step") or 1)
    # исправление ответа закрыло рекомендацию — фиксируем и хвалим
    for rid, meta in saved.items():
        if rid not in cand and meta.get("status") in ("new", "in_progress", "deferred", "after_launch", None) \
                and meta.get("seen"):
            meta.update({"status": "done", "verified": True, "auto": True,
                         "praise": _ack(meta.get("rec_snapshot") or {}, rid), "updated": now()})
    items = []
    for rid, rec in cand.items():
        meta = saved.setdefault(rid, {"status": "new", "created": now()})
        meta["seen"] = True
        if meta.get("status") == "done" and meta.get("verified"):
            meta["status"] = "in_progress"  # условие вернулось — рекомендация снова актуальна
            meta["verified"] = False
        items.append({**rec, **{k: meta.get(k) for k in ("status", "owner", "reason", "defer_step", "stage", "enriched",
                                                          "note")},
                      "score": round(_score(rec, cur), 3), "path": path(rec, meta, cur >= core.step_ids()[-1])})
    # закрытые, но уже ушедшие из кандидатов — для ленты успехов
    done_feed = [{"id": rid, "title": m.get("title_snapshot", ""), "praise": m.get("praise", ""), "updated": m.get("updated")}
                 for rid, m in saved.items() if m.get("status") == "done" and rid not in cand]
    visible = [i for i in items if i["status"] in ("new", "in_progress")
               or (i["status"] == "deferred" and cur >= (i.get("defer_step") or 99))]
    for i in items:
        saved[i["id"]]["title_snapshot"] = i["title"]
        saved[i["id"]]["rec_snapshot"] = {"effort": i["effort"], "source": i["source"]}
    visible.sort(key=lambda i: (-(i["status"] == "in_progress"), -i["score"], i["step"]))
    nxt = _pick_next(visible)
    if not nxt:
        # страховка: всё отложено или отклонено — навигация показывается всё равно, отклонить её навсегда нельзя
        prim = next((i for i in items if i.get("nav") == "primary"), None)
        if prim:
            nxt = [{**prim, "fallback": True}]
    plan = {}
    for i in sorted(items, key=lambda i: (i["step"], -i["score"])):
        if i["status"] != "after_launch":
            plan.setdefault(i["code"], []).append(i)
    # план после запуска — по стадиям в порядке запуска; в «Следующий шаг» не попадает, но и не теряется
    after = sorted((i for i in items if i["status"] == "after_launch"),
                   key=lambda i: (STAGES.index(i["stage"]) if i.get("stage") in STAGES else 9, i["step"]))
    counts = {s: sum(1 for i in items if i["status"] == s)
              for s in ("new", "in_progress", "deferred", "after_launch")}
    # снятые считаются по всем сохранённым, а не по текущим кандидатам: условие ушло — причина и запись остаются
    counts["dismissed"] = sum(1 for m in saved.values() if m.get("status") == "dismissed")
    counts["done"] = sum(1 for m in saved.values() if m.get("status") == "done")
    dismissed_feed = sorted(({"id": rid, "title": m.get("title_snapshot") or "", "reason": m.get("reason") or "",
                              "updated": m.get("updated")}
                             for rid, m in saved.items() if m.get("status") == "dismissed"),
                            key=lambda x: x.get("updated") or "", reverse=True)
    return {"next": nxt, "plan": plan, "after_launch": after, "counts": counts, "done_feed": done_feed[-10:],
            "dismissed_feed": dismissed_feed[:20]}


def _pick_next(visible: list[dict], n: int = 3) -> list[dict]:
    """Топ-3 по оценке, но навигация в нём есть всегда: главный ход (доработать текущий шаг или перейти дальше)
    и самый ранний незакрытый прошлый шаг. Порядок — как в отсортированном списке."""
    must = [i["id"] for i in visible if i.get("nav") == "primary"][:1]
    ret = sorted((i for i in visible if i.get("nav") == "return"), key=lambda i: i["step"])
    if ret:
        must.append(ret[0]["id"])
    chosen = set(must)
    for i in visible:
        if len(chosen) >= n:
            break
        chosen.add(i["id"])
    return [i for i in visible if i["id"] in chosen]


RISK_PATH = ("Важное до запуска отложить нельзя — его закрывают или принимают риском. Путь: 1) сделайте по «Как сделать» "
             "в рекомендации; 2) если закрыть до запуска не выходит — на шаге {code} в вопросе «Реестр рисков» добавьте "
             "строку «действие · риск · контроль, который ловит · владелец (роль)», затем в меню «⋯» нажмите «Не актуально» "
             "с причиной «принято риском, строка в реестре рисков».")


def _defer(st: dict, meta: dict, rec: dict, stage: str, owner: str) -> str:
    """Отложить. До последнего шага — до следующего шага, с путём «как закрыть раньше».
    На последнем шаге откладывать некуда: пункт уходит в план после запуска — с владельцем (роль) и стадией запуска,
    к переходу на которую он закрывается. Важное до запуска и навигацию по маршруту отложить нельзя."""
    ids = core.step_ids()
    cur = int(st.get("current_step") or 1)
    if rec.get("nav"):
        raise ValueError("Это ход по маршруту — отложить его некуда. Не подходит — «Не актуально» с причиной; "
                         "если других рекомендаций не останется, он вернётся сам.")
    target = min(ids[-1], max(cur + 1, rec.get("step", 1)))
    if target > cur:
        meta["status"] = "deferred"
        meta["defer_step"] = target
        first = (rec.get("how") or [""])[0]
        return (f"Отложено до шага {core.code(target)}: вернётся в «Следующий шаг», когда вы его откроете. "
                + (f"Закрыть раньше: {first}" if first else "Путь — в рекомендации, раздел «Как сделать»."))
    if rec.get("level") == "warning":
        raise ValueError(RISK_PATH.format(code=core.code(core.qstep("risk_table"))))
    stage, owner = stage.strip(), (owner or meta.get("owner") or "").strip()
    if stage not in STAGES or not owner:
        raise ValueError("На последнем шаге отложить можно только в план после запуска: укажите владельца (роль) и "
                         f"стадию запуска, до перехода на которую пункт закрывается — {', '.join(STAGES)}.")
    meta.update({"status": "after_launch", "stage": stage, "owner": owner[:120]})
    meta.pop("defer_step", None)
    return (f"В плане после запуска: закрыть до перехода на стадию «{stage}», владелец — {owner}. Пункт виден в «План "
            "развития» → «После запуска» с путём по шагам и попадёт в каркас защиты и паспорт проекта.")


def after_launch_lines(st: dict) -> list[str]:
    """План после запуска для каркаса защиты и паспорта: «стадия · что · владелец». Только чтение состояния."""
    rows = [m for m in (st.get("recs") or {}).values() if m.get("status") == "after_launch"]
    rows.sort(key=lambda m: STAGES.index(m["stage"]) if m.get("stage") in STAGES else 9)
    return [f"до стадии «{m.get('stage')}» · {m.get('title_snapshot') or 'рекомендация'} · владелец — {m.get('owner')}"
            for m in rows]


def act(st: dict, rid: str, action: str, reason: str = "", owner: str = "") -> dict:
    """start | done | defer | dismiss | reopen. Возвращает итог для показа Junior Pilot."""
    cand = candidates(st)
    saved = st.setdefault("recs", {})
    if rid not in cand and rid not in saved:
        raise ValueError("нет такой рекомендации")
    meta = saved.setdefault(rid, {"status": "new", "created": now()})
    rec = cand.get(rid, {})
    hist = meta.setdefault("history", [])
    if owner:
        meta["owner"] = owner.strip()[:120]
    if action == "start":
        meta["status"] = "in_progress"
        msg = "Взято в работу. План и образец — по кнопке «План от Senior Pilot»."
    elif action == "done":
        # пункт плана после запуска закрывается работой вне copilot — отметку владельца принимаем
        if rid in cand and rec.get("source") not in SELF_CHECKED and meta.get("status") != "after_launch":
            meta["status"] = "in_progress"
            meta["verified"] = False
            hint = (rec.get("how") or [""])[0]
            msg = ("Пока не видно изменений в проекте: условие, из-за которого появилась рекомендация, ещё на месте. "
                   + (f"Что поменять: {hint}" if hint else "Проверьте ответ на шаге " + rec.get("code", "") + "."))
        else:
            meta.update({"status": "done", "verified": rid not in cand or rec.get("source") in SELF_CHECKED,
                         "self_marked": meta.get("status") == "after_launch" or None})
            meta["praise"] = _ack(rec or meta.get("rec_snapshot") or {}, rid)
            msg = meta["praise"]
    elif action == "defer":
        msg = _defer(st, meta, rec, reason, owner)
    elif action == "dismiss":
        if not reason.strip():
            raise ValueError("укажите причину — так рекомендация не вернётся без повода")
        meta.update({"status": "dismissed", "reason": reason.strip()[:300]})
        msg = "Отмечено как неактуальное. Причина сохранена."
        if rec.get("nav") == "primary":
            msg += " Это подсказка, куда идти дальше: если других рекомендаций не останется, она вернётся сама."
    elif action == "reopen":
        meta["status"] = "new"
        msg = "Рекомендация снова в плане."
    else:
        raise ValueError("действие: start, done, defer, dismiss или reopen")
    meta["updated"] = now()
    hist.append({"ts": meta["updated"], "action": action, "reason": reason[:200]})
    return {"message": msg, "status": meta["status"], "verified": meta.get("verified")}


ENRICH_FORMAT = """Переведи рекомендацию на язык этого проекта. Ответь строго одним JSON-объектом:
{"why_here": "одно предложение: зачем это именно этому проекту", "plan": ["3–5 коротких действий по порядку"],
 "example": "образец результата для этого проекта, 1–3 строки",
 "praise": "пустая строка; одно короткое предложение — только если в проекте уже есть сложное или нестандартное решение по этой теме"}
Числа бери только из ответов проекта; если числа нет — «[число: где взять]». Кавычки внутри текста — «ёлочки»."""


def enrich(st: dict, rid: str, router=None) -> dict:
    """План от Senior Pilot: рекомендация на языке проекта. Один вызов модели, результат сохраняется."""
    from . import senior
    from .router import Router, RouterError, extract_json
    rec = candidates(st).get(rid)
    if not rec:
        raise ValueError("рекомендация уже не актуальна")
    user = (f"Контекст проекта:\n{senior.project_context(st)}\n\n"
            f"Рекомендация (шаг {rec['code']}): {rec['title']}\nЗачем: {rec['why']}\n"
            f"Подсказки ядра: {'; '.join(rec['how'])}\n\n{ENRICH_FORMAT}")
    router = router or Router()
    r = router.chat("senior", senior._system(), [{"role": "user", "content": user}], max_tokens=2000, temperature=0.2,
                    purpose=f"план по рекомендации {rec['code']}")
    try:
        data = extract_json(r.text)
    except (ValueError, json.JSONDecodeError):
        raise RouterError("Senior Pilot ответил не по формату — попробуйте ещё раз")
    data = guard.filter_obj({k: data.get(k) for k in ("why_here", "plan", "example", "praise")})
    if not isinstance(data.get("plan"), list):
        data["plan"] = [str(data.get("plan") or "")]
    st.setdefault("recs", {}).setdefault(rid, {"status": "new", "created": now()})["enriched"] = data
    return {**data, "cost_usd": r.cost_usd, "model": r.model}
