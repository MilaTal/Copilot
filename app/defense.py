"""Подготовка к защите перед ЛПР (скилл ai-project-review): работа руководителя, каркас на 3–4 слайда,
таблица сверки чисел, пустые места и проверка ответов на пять вопросов ЛПР.

Правила:
  — числа берутся только из источника (ответы проекта или загруженные документы), не пересчитываются и не округляются;
  — после каждого числа — откуда оно; расчёт допускается один — стоимость успешного результата по серии прогонов;
  — на слайде заголовок-вывод с числом и не больше четырёх строк; ноды, модели и сервисы не выносятся;
  — пробел помечается [ПУСТО: что дописать и где], а не заполняется догадкой."""
from __future__ import annotations

import re
from datetime import datetime

from . import core, evalrun, readiness
from . import guard
from .router import Router, extract_json
from .state import fmt_date, get_answer, project_dir

NUM = re.compile(r"(?<![\w.,])(?:\d{1,2}\.\d{1,2}\.\d{4}|\d+(?:[ \u00a0]\d{3})*(?:[.,]\d+)?)(?![\w.])")
MAX_LINES = 4


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip()


def _numbers(s: str) -> list[str]:
    """Числа целиком: «3» не находится внутри «360», «17,5» — одно число."""
    return [m.group().strip() for m in NUM.finditer(s or "")]


def _num_key(n: str) -> str:
    return n.replace(" ", "").replace("\u00a0", "").replace(",", ".")


def _ans(st, qid):
    rec = get_answer(st, core.qstep(qid), qid)
    if not rec or rec["status"] not in ("answered", "needs_detail"):
        return None
    return rec["value"]


def _ref(qid: str, label: str) -> str:
    """Подпись источника числа: «Ш2 · KPI» — код шага из ядра."""
    return f"{core.code(core.qstep(qid))} · {label}"


def _where(qid: str, label: str) -> str:
    return f"{core.code(core.qstep(qid))}, {label}"


def facts_from_state(st: dict) -> dict:
    """Факты для защиты из ответов проекта. Каждый факт — текст как в источнике и ссылка на источник."""
    f: dict[str, list[dict]] = {}

    def add(key, text, ref):
        if text:
            f.setdefault(key, []).append({"text": _norm(text), "ref": ref})

    add("loss", _ans(st, "loss_now"), _ref("loss_now", "потеря сейчас"))
    add("bottleneck", _ans(st, "bottleneck"), _ref("bottleneck", "узкое место"))
    add("decision_maker", _ans(st, "decision_maker"), _ref("decision_maker", "кто поддержит"))
    kpis = _ans(st, "kpi_table") or []
    if kpis:  # первая строка KPI — главное число ЛПР
        add("business_kpi", kpis[0][0] if kpis[0] else "", _ref("kpi_table", "главное число ЛПР"))
    add("agent_does", _ans(st, "core_step"), _ref("core_step", "ядро агента"))
    add("human_left", _ans(st, "human_left"), _ref("human_left", "оставлено человеку"))
    for i, r in enumerate(kpis, 1):
        add("kpi", f"{r[0]}: {r[1]} → {r[2]}" + (f" ({r[3]})" if len(r) > 3 and r[3] else ""), _ref("kpi_table", f"KPI, строка {i}"))
    add("scope", _ans(st, "pilot_scope"), _ref("pilot_scope", "рамка пилота"))
    for i, r in enumerate(_ans(st, "rollout_table") or [], 1):
        add("rollout", f"{r[0]}: {r[1]}", _ref("rollout_table", f"стадии, строка {i}"))
    for i, r in enumerate(_ans(st, "risk_table") or [], 1):
        if len(r) >= 4:
            add("risk", f"{r[1]} — контроль: {r[2]}; владелец: {r[3]}", _ref("risk_table", f"реестр рисков, строка {i}"))
    cs = evalrun.cost_of_success(_ans(st, "runs_table") or [])
    if cs["per_success_usd"] is not None:
        add("cost", f"{cs['per_success_usd']} $ за успешный результат: {cs['total_usd']} $ за серию прогонов "
                    f"({cs['runs']}), успешных {cs['successful']}", "расчёт по " + _ref("runs_table", "серия прогонов"))
    add("ownership", _ans(st, "ownership"), _ref("ownership", "владение"))
    add("ask", _ans(st, "decision_ask"), _ref("decision_ask", "просьба к ЛПР"))
    add("security", _ans(st, "security_checklist"), _ref("security_checklist", "ответы ИБ"))
    runs = st.get("attack_runs") or []
    if runs:
        last = runs[-1]["summary"]
        ok = sum(1 for v in last.values() if v == "passed")
        bad = sum(1 for v in last.values() if v in ("failed", "inconclusive"))
        untested = sum(1 for v in last.values() if v == "unreachable")
        add("attacks", f"испытания: пройдено {ok} из {len(last)}, не пройдено или не доказано {bad}"
                       + (f", не проверено (стенд недоступен) — {untested}" if untested else ""),
            _ref("attack_selection", f"протокол {fmt_date(runs[-1]['ts'])}"))
    evals = st.get("eval_runs") or []
    if evals:
        s = evals[-1]["summary"]
        add("eval", f"набор проверок: пройдено {s['passed']} из {s['total']}", _ref("eval_table", f"прогон {fmt_date(s['ts'])}"))
    return f


def _headline(text: str, limit: int = 110) -> str | None:
    """Заголовок-вывод: первый фрагмент источника, где есть число. Текст не переписывается."""
    for part in re.split(r"(?<=[.;])\s+|\s—\s", text):
        if any(ch.isdigit() for ch in part):
            part = part.strip(" .;")
            return part if len(part) <= limit else part[: limit - 1] + "…"
    return None


def _cut(text: str, limit: int = 220) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > limit // 2 else cut).rstrip(" ,;—") + "…"


def _line(fact: dict) -> str:
    return f"{_cut(fact['text'])} ({fact['ref']})"


def _dedup(lines: list[str]) -> list[str]:
    out = []
    for x in lines:
        if x not in out:
            out.append(x)
    return out


def build_slides(f: dict) -> tuple[list[dict], list[str]]:
    gaps = []

    def gap(what, where):
        gaps.append(f"[ПУСТО: {what} — {where}]")
        return gaps[-1]

    slides = []
    # 1 · проблема и цена «оставить как есть»
    loss = (f.get("loss") or [None])[0]
    title = _headline(loss["text"]) if loss else None
    lines = [_line(x) for x in (f.get("bottleneck") or [])[:1]]
    if loss and title != loss["text"]:
        lines.insert(0, _line(loss))
    if not title:
        title = gap("оценка потерь сейчас в неделю или месяц", _where("loss_now", "вопрос о потерях"))
    slides.append({"n": 1, "block": "Проблема и цена «оставить как есть»", "title": title,
                   "title_ref": loss["ref"] if loss else "", "lines": _dedup(lines)[:MAX_LINES]})
    # 2 · что меняется и как измерим
    kpis = f.get("kpi") or []
    title = _headline(kpis[0]["text"]) if kpis else gap("KPI со значением «до» и целью", _where("kpi_table", "KPI проекта"))
    lines = [_line(x) for x in (f.get("agent_does") or [])[:1] + kpis[:2] + (f.get("human_left") or [])[:1]]
    slides.append({"n": 2, "block": "Что меняется и как измерим", "title": title,
                   "title_ref": kpis[0]["ref"] if kpis else "", "lines": _dedup(lines)[:MAX_LINES]})
    # 3 · как запускаем без риска
    roll = f.get("rollout") or []
    title = _headline(roll[0]["text"]) if roll else gap("стадии запуска с критерием перехода числом", _where("rollout_table", "запуск по стадиям"))
    lines = [_line(x) for x in roll[:2] + (f.get("risk") or [])[:1] + (f.get("attacks") or f.get("scope") or [])[:1]]
    slides.append({"n": 3, "block": "Как запускаем без риска", "title": title,
                   "title_ref": roll[0]["ref"] if roll else "", "lines": _dedup(lines)[:MAX_LINES]})
    # 4 · стоимость и решение
    cost = f.get("cost") or []
    ask = (f.get("ask") or [None])[0]
    title = (_headline(ask["text"]) if ask else None) or (_headline(cost[0]["text"]) if cost else None)
    title_ref = (ask["ref"] if ask and _headline(ask["text"]) else (cost[0]["ref"] if cost else ""))
    if not title:
        title = gap("одно решение, которое просим, и число, ради которого", _where("decision_ask", "просьба к ЛПР"))
    lines = [_line(x) for x in cost[:1] + (f.get("ownership") or [])[:1] + (f.get("ask") or [])[:1]]
    if not cost:
        lines.append(gap("стоимость успешного результата", _where("runs_table", "серия прогонов")))
    slides.append({"n": 4, "block": "Стоимость и решение", "title": title, "title_ref": title_ref,
                   "lines": _dedup(lines)[:MAX_LINES]})
    return slides, gaps


def _quote_around(source: str, num: str, width: int = 80) -> str:
    """Цитата — окно вокруг числа, чтобы само число было видно, по границам слов."""
    for m in NUM.finditer(source):
        if _num_key(m.group()) == _num_key(num):
            a = max(0, m.start() - width // 2)
            b = min(len(source), m.end() + width // 2)
            if a > 0:
                sp = source.find(" ", a)
                a = sp + 1 if 0 <= sp < m.start() else a
            if b < len(source):
                sp = source.rfind(" ", m.end(), b)
                b = sp if sp > m.end() else b
            return ("…" if a > 0 else "") + source[a:b].strip() + ("…" if b < len(source) else "")
    return source[:width]


def reconcile(slides: list[dict], f: dict) -> list[dict]:
    """Каждое число на слайде ищется целиком в СВОЁМ источнике — том, что указан в скобках строки
    (у заголовка — источник заголовка). Число без своего источника — расхождение. Дубли не считаются."""
    by_ref: dict[str, list[str]] = {}
    for xs in f.values():
        for x in xs:
            by_ref.setdefault(x["ref"], []).append(x["text"])
    rows, seen = [], set()

    def check(text: str, ref: str, slide_n: int):
        sources = by_ref.get(ref, [])
        for num in _numbers(text):
            key = (slide_n, _num_key(num), ref)
            if key in seen:
                continue
            seen.add(key)
            hit = next((s for s in sources if _num_key(num) in {_num_key(n) for n in _numbers(s)}), None)
            rows.append({"number": num, "slide": slide_n, "source": ref or "—",
                         "quote": (_quote_around(hit, num) if hit else ""), "match": bool(hit)})

    for s in slides:
        if not s["title"].startswith("[ПУСТО"):
            check(s["title"], s.get("title_ref", ""), s["n"])
        for line in s["lines"]:
            if line.startswith("[ПУСТО"):
                continue
            m = re.search(r"\s\(([^()]*)\)$", line)
            ref = m.group(1) if m else ""
            body = line[: m.start()] if m else line
            check(body, ref, s["n"])
    return rows


def lpr_questions() -> list[tuple[str, list[str], str]]:
    """Пять вопросов ЛПР и где в маршруте ответ — коды шагов из ядра."""
    c = lambda qid: core.code(core.qstep(qid))  # noqa: E731
    return [
        ("Почему не оставить как есть?", ["loss"], f"оценка потерь — {c('loss_now')}"),
        ("А если агента взломают или он ошибётся?", ["attacks", "security", "risk"],
         f"испытания {c('attack_selection')} или ответы ИБ {c('security_checklist')}"),
        ("Как это пустить в массы?", ["scope", "rollout"], f"рамка пилота и стадии запуска — {c('pilot_scope')}"),
        ("Сколько это стоит в эксплуатации?", ["cost", "ownership"],
         f"стоимость результата {c('runs_table')} и владение {c('ownership')}"),
        ("Как поймём, что работает?", ["kpi", "eval"], f"KPI {c('kpi_table')} и прогон проверок {c('eval_table')}"),
    ]


def lpr_check(f: dict) -> list[dict]:
    out = []
    for q, keys, where in lpr_questions():
        hit = next((x for k in keys for x in f.get(k, []) if _numbers(x["text"])), None)
        out.append({"question": q, "answer": _line(hit) if hit else f"[ПУСТО: ответ числом — {where}]", "ok": bool(hit)})
    return out


def jtbd(f: dict) -> dict:
    def g(key, where):
        x = (f.get(key) or [None])[0]
        return x["text"] if x else f"[ПУСТО: {where}]"
    c = lambda qid: core.code(core.qstep(qid))  # noqa: E731
    return {
        "who": g("decision_maker", f"кто поддержит проект — {c('decision_maker')}"),
        "when": g("loss", f"ситуация и потеря — {c('loss_now')}"),
        "wants": g("business_kpi", f"число, по которому ЛПР поймёт успех, — первая строка KPI, {c('kpi_table')}"),
        "so_that": g("ask", f"решение, которое просим — {c('decision_ask')}"),
        "hired_now": g("bottleneck", f"как задача решается сейчас и сколько стоит — {c('bottleneck')}"),
        "holds_back": g("risk", f"что может удержать — {c('risk_table')}, реестр рисков"),
        "relieves": g("human_left", f"какие решения остаются человеку — {c('human_left')}"),
    }


def go_no_go(slides, rec, gaps, lpr, blockers=None) -> dict:
    """Совет, а не запрет. Критичные открытые риски (непройденные испытания, проверки, разработка) — «не взлетаем»."""
    blockers = blockers or []
    mismatches = sum(1 for r in rec if not r["match"])
    missing_core = any(s["title"].startswith("[ПУСТО") for s in slides[:2])
    unanswered = sum(1 for x in lpr if not x["ok"])
    if blockers:
        verdict, text = "no_go", ("Пока рано на защиту — сначала закройте важное: " + "; ".join(blockers[:3])
                                  + ("…" if len(blockers) > 3 else ""))
    elif missing_core or mismatches:
        verdict, text = "no_go", "Пока рано на защиту: нужно главное число, а числа на слайдах должны совпасть с источником."
    elif gaps or unanswered:
        verdict, text = "caution", f"Можно готовиться к защите. Рекомендации: заполнить пустых мест — {len(gaps)}, ответить числом на вопросов ЛПР — {unanswered}."
    else:
        verdict, text = "go", "Готово к защите: все числа сверены, на пять вопросов ЛПР есть ответ числом."
    return {"verdict": verdict, "text": text, "mismatches": mismatches, "gaps": len(gaps), "lpr_unanswered": unanswered,
            "blockers": blockers}


def build(f: dict, title: str, blockers: list[str] | None = None) -> dict:
    slides, gaps = build_slides(f)
    rec = reconcile(slides, f)
    lpr = lpr_check(f)
    j = jtbd(f)
    all_gaps = list(gaps)
    for v in j.values():
        if v.startswith("[ПУСТО") and v not in all_gaps:
            all_gaps.append(v)
    for x in lpr:
        if not x["ok"] and x["answer"] not in all_gaps:
            all_gaps.append(x["answer"])
    go = go_no_go(slides, rec, all_gaps, lpr, blockers or [])
    # Каркас уже собран: предупреждение не должно звучать так, будто собирать было нечего.
    if go["verdict"] == "no_go" and slides:
        go["text"] = (f"Каркас собран, слайдов — {len(slides)}. Перед показом ЛПР закройте важное: "
                      + "; ".join((blockers or [])[:3] or ["главное число и сверку чисел"]))
    return {"title": title, "jtbd": j, "slides": slides, "reconcile": rec, "gaps": all_gaps, "lpr": lpr,
            "go": go}


def to_markdown(d: dict) -> str:
    j = d["jtbd"]
    L = [f"# Каркас защиты — {d['title']}", "", f"Собрано {datetime.now().strftime('%d.%m.%Y %H:%M')}. {d['go']['text']}", "",
         "## Работа руководителя", "",
         f"Кто решает: {j['who']}", f"Когда: {j['when']}", f"Хочет — и по какому числу поймёт: {j['wants']}",
         f"Чтобы: {j['so_that']}", f"Чем решает задачу сейчас: {j['hired_now']}",
         f"Что может удержать: {j['holds_back']}", f"Что это снимает: {j['relieves']}", ""]
    for s in d["slides"]:
        L += [f"## Слайд {s['n']} · {s['block']}", "", f"**{s['title']}**", ""] + [f"- {x}" for x in s["lines"]] + [""]
    L += ["## Сверка чисел", "", "| число | слайд | источник | цитата | совпадает |", "|---|---|---|---|---|"]
    for r in d["reconcile"]:
        L.append(f"| {r['number']} | {r['slide']} | {r['source']} | {r['quote'].replace('|', '/')} | {'да' if r['match'] else 'нет'} |")
    L += ["", "## Пять вопросов ЛПР — ответ числом", ""] + [f"- {x['question']} — {x['answer']}" for x in d["lpr"]]
    if d.get("after_launch"):
        L += ["", "## План после запуска", ""] + [f"- {x}" for x in d["after_launch"]]
    L += ["", "## Пустые места", ""] + ([f"- {g}" for g in d["gaps"]] or ["Нет."])
    n = len(d["reconcile"])
    bad = sum(1 for r in d["reconcile"] if not r["match"])
    L += ["", f"Сверено чисел: {n}, разошлось: {bad}, пустых мест: {len(d['gaps'])}."]
    return "\n".join(L) + "\n"


def for_project(st: dict) -> dict:
    rs = readiness.risks(st)
    blockers = [f"{r['code']}: {r['text'][:90]}" for r in rs if r["level"] == "warning"
                and (r["source"] in ("attack", "eval", "workspace", "consistency")
                     or r["reason"] == "Выбран рискованный вариант")]
    if not st.get("attack_runs"):
        blockers.append(f"{core.code(core.qstep('attack_selection'))}: испытания ещё не прогонялись")
    d = build(facts_from_state(st), st["name"], blockers)
    from .recommend import after_launch_lines  # recommend импортирует readiness; здесь — без цикла при загрузке
    d["after_launch"] = after_launch_lines(st)
    md = to_markdown(d)
    path = project_dir(st["id"]) / "docs" / "Каркас защиты.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    d["markdown"] = md
    d["path"] = path.name
    d["readiness"] = readiness.overall(st)
    return d


EXTRACT_PROMPT = """Из документов проекта выпиши факты для защиты перед руководителем. Ничего не считай и не округляй.
Ответь одним JSON-объектом: {"facts": [{"key": "<ключ>", "quote": "<дословная цитата из документа, до 25 слов>"}]}
Ключи: loss (потери сейчас), bottleneck (узкое место), decision_maker (кто решает), business_kpi, agent_does (что делает агент),
human_left (что остаётся человеку), kpi (KPI до и цель), scope (рамка пилота), rollout (стадии запуска и критерии),
risk (риск, контроль, владелец), cost (стоимость результата), ownership (эксплуатация, смена модели), ask (что просим),
security (безопасность), eval (проверка качества). Цитата обязана дословно встречаться в документе."""


def from_documents(text: str, title: str, router: Router | None = None, only_local: bool = True) -> dict:
    """Режим без проекта: факты из загруженных документов. Каждая цитата проверяется кодом на дословное вхождение."""
    router = router or Router()
    r = router.chat("senior", "Ты аккуратно извлекаешь факты из документов и ничего не придумываешь.",
                    [{"role": "user", "content": f"{EXTRACT_PROMPT}\n\n" + guard.wrap_data("Документы проекта", text[:80000])}],
                    max_tokens=3000, temperature=0, purpose="факты для защиты", only_local=only_local)
    data = extract_json(r.text)
    norm_src = _norm(text)
    f, rejected = {}, []
    for item in data.get("facts") or []:
        q = _norm(item.get("quote", ""))
        key = item.get("key", "")
        if q and q in norm_src:
            f.setdefault(key, []).append({"text": q, "ref": "документы, цитата"})
        else:
            rejected.append(q[:120])
    d = build(f, title)
    d["rejected_quotes"] = rejected
    d["markdown"] = to_markdown(d) + ("\n## Цитаты, не найденные в документах\n\n" + "\n".join(f"- {x}" for x in rejected) + "\n"
                                      if rejected else "")
    d["model"] = r.model
    return d
