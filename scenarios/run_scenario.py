"""Прогон полного цикла по сценарию: ответы Junior Pilot → проверка Senior Pilot → итоги шагов → варианты →
противоречия → защита. Отчёт оценивает вердикты и критичность Senior Pilot против разметки сценария.

Запуск:
  python -m scenarios.run_scenario demo_scenario [флаги]

Флаги:
  --dry-run   Бесплатно, без единого вызова модели: ответы записываются по шагам, у каждого шага проверяется,
              что все обязательные вопросы закрыты, у таблиц хватает строк и есть обязательные строки, и собирается
              документ шага. Проверка Senior Pilot, итоги шагов и варианты пропускаются, отчёт .md не пишется.
  --keep      Проект НЕ уходит в архив тестовых прогонов: создаётся с именем из сценария (project.name) и остаётся
              в списке проектов. Без --keep проект прогона после прогона (и при сбое) переносится в
              data/_archive_test_projects/<дд.мм.гггг>/.
  --attacks   Ветка атак — испытания прототипа после основного прогона механизмом приложения (app/attacks.py:
              предохранитель → ключ пуска → пуск). Запускается ТОЛЬКО при двух раздельных подтверждениях:
                --confirm-attacks-1        первое — «снять предохранитель», план испытаний принят;
                --confirm-attacks-2 ХОСТ   второе — «пуск»: хост стенда, набранный вручную (например 127.0.0.1:8100),
                                           и тем самым отметка, что это стенд, а не рабочая система.
              Без любого из них атаки не запускаются (отказ с объяснением), основной прогон при этом идёт.
              Атаки идут только против стенда: адрес — из раздела stand сценария или из ответа на вопрос endpoint
              (Ш4); стенд не задан, место испытаний (attack_target, Ш5) похоже на рабочую систему или хост не назван
              там как стенд — отказ. Находки пишутся в проект: риски Ш5, история испытаний, протокол в docs/.
              Цена ветки атак — запросы к стенду; модель прототипа на стенде локальная, вызовов роутера copilot нет.
  --rework-only ID_ПРОЕКТА
              Только круг доработки для проекта, который уже прошёл платную проверку: ответы со статусом
              «нужно уточнить», у которых в сценарии есть rework, заменяются доработанными и проверяются Senior Pilot
              снова, затем собираются документы всех шагов. Остальные ответы повторно не проверяются, новый проект не
              создаётся, проект остаётся в списке. Отчёт — «доработка <сценарий> <дата>.md» рядом со сценарием.
              Цена: по одной проверке на каждый ответ «нужно уточнить», у которого есть доработка.
              Вместе с --dry-run — без модели: доработка считается принятой, документы собираются.

Круг доработки (с 12.10.2026). После проверки шага ответ с вердиктом needs_detail, у которого в сценарии есть поле
rework, заменяется доработанным и проверяется снова тем же senior.review_answer; в --dry-run вердикт берётся из
разметки (expect: needs_detail), а доработка считается принятой без модели. Затем собирается документ шага.
В отчёте — раздел «Доработка после замечаний»: вопрос · замечание Senior Pilot · доработанный ответ · вердикт.

Цена основного прогона без --dry-run зависит от выбранной модели: по одной проверке на ответ, повторные проверки
круга доработки, итоги шагов и наборы вариантов. Расход виден на экране «Модели».

Примеры:
  python -m scenarios.run_scenario demo_scenario --dry-run
  python -m scenarios.run_scenario demo_scenario --keep
  python -m scenarios.run_scenario demo_scenario --keep --attacks --confirm-attacks-1 --confirm-attacks-2 127.0.0.1:8100
  python -m scenarios.run_scenario demo_scenario --rework-only ID_ПРОЕКТА

Формат сценария. «schema: 3» — сценарий маршрута из 6 шагов, ответы читаются напрямую. Без schema (или schema < 3) —
сценарий прежнего маршрута из 12 шагов: пары «шаг:вопрос» переводятся по таблице ядра (migration.map), слитые
вопросы в прогон не идут и перечисляются в отчёте. Разметка ответа: expect — accepted | needs_detail
(прежние accept | detail тоже понимаются), flaw — заложенная ошибка и ожидаемый сигнал (senior | risk | consistency)."""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml

HERE = Path(__file__).resolve().parent
URL_RE = re.compile(r"https?://[^\s·,;«»\"'<>()]+")
CONFIRM_TEXT = ("нужны два раздельных подтверждения пользователя: --confirm-attacks-1 (снять предохранитель — план "
                "испытаний принят) и --confirm-attacks-2 ХОСТ (пуск — хост стенда, набранный вручную, это не рабочая "
                "система)")


# ---------- сценарий ----------
def load_scenario(name: str) -> dict:
    """Имя сценария из папки scenarios или путь к файлу .yaml."""
    p = Path(name)
    path = p if p.suffix in (".yaml", ".yml") and p.exists() else HERE / f"{name}.yaml"
    sc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(sc, dict) or not isinstance(sc.get("answers"), list) or not isinstance(sc.get("project"), dict):
        raise ValueError(f"сценарий {path.name}: нужны разделы project и answers")
    return sc


def is_native(sc: dict) -> bool:
    return int(sc.get("schema", 2) or 2) >= 3


def norm_expect(x) -> str:
    return "needs_detail" if str(x or "").strip() in ("detail", "needs_detail") else "accepted"


def to_route(sc: dict) -> tuple[list[dict], list[dict]]:
    """Ответы сценария — на шаги текущего ядра. Сценарий schema: 3 читается напрямую. Сценарий прежнего маршрута
    переводится по таблице ядра (migration.map); слитые вопросы возвращаются вторым списком для отчёта."""
    from app import core
    if is_native(sc):
        return [{**a, "step": int(a["step"])} for a in sc["answers"]], []
    mig = core.migration()
    qmap, rules = mig.get("map") or {}, mig.get("merge_rules") or {}
    out, skipped = [], []
    for a in sc["answers"]:
        key = f"{a['step']}:{a['qid']}"
        if key in rules or key not in qmap:
            skipped.append(a)
            continue
        ns, nq = qmap[key].split(":")
        out.append({**a, "step": int(ns), "qid": nq, "old": key})
    return out, skipped


# ---------- флаги ----------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m scenarios.run_scenario",
                                 description="Прогон полного цикла copilot по сценарию (см. шапку файла).")
    ap.add_argument("scenario", nargs="?", default="demo_scenario", help="имя сценария в scenarios/ или путь .yaml")
    ap.add_argument("--dry-run", action="store_true", help="без вызовов модели: структура и документы шагов")
    ap.add_argument("--keep", action="store_true", help="проект остаётся в списке проектов, не уходит в архив")
    ap.add_argument("--attacks", action="store_true", help="ветка атак против стенда (нужны оба подтверждения)")
    ap.add_argument("--confirm-attacks-1", dest="confirm1", action="store_true",
                    help="первое подтверждение: снять предохранитель")
    ap.add_argument("--confirm-attacks-2", dest="confirm2", nargs="?", const=True, default=None, metavar="ХОСТ",
                    help="второе подтверждение: хост стенда, набранный вручную")
    ap.add_argument("--rework-only", dest="rework_only", default=None, metavar="ID_ПРОЕКТА",
                    help="только круг доработки слабых ответов уже проверенного проекта и сборка документов шагов")
    return ap.parse_args(argv)


def attack_gate(args: argparse.Namespace) -> str | None:
    """Текст отказа, если ветку атак запускать нельзя по подтверждениям; None — подтверждения есть."""
    if not args.attacks:
        return None
    if not args.confirm1 or args.confirm2 in (None, ""):
        return "Атаки не запущены: " + CONFIRM_TEXT + "."
    if args.confirm2 is True:
        return ("Атаки не запущены: второе подтверждение — это хост стенда, набранный вручную, например "
                "--confirm-attacks-2 127.0.0.1:8100.")
    return None


# ---------- стенд ----------
def stand_target(st: dict, sc: dict) -> dict | None:
    """Адрес стенда: раздел stand сценария, иначе адрес из ответа endpoint (Ш4), иначе уже сохранённый в проекте."""
    from app import core, state, target
    raw = dict(sc.get("stand") or {})
    if not raw.get("url"):
        rec = state.get_answer(st, core.qstep("endpoint"), "endpoint")
        text = str((rec or {}).get("value") or "")
        m = URL_RE.search(text)
        if m:
            raw["url"] = m.group(0).rstrip(".")
            if re.match(r"\s*GET\b", text):
                raw["method"] = "GET"
    if not raw.get("url"):
        raw = dict(st.get("target") or {})
    if not raw.get("url"):
        return None
    try:
        return target.normalize(raw)
    except ValueError:
        return None


def _loopback(host: str) -> bool:
    if host in ("localhost",) or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def stand_refusal(st: dict, tgt: dict | None) -> str | None:
    """Ветка атак работает только против стенда. Текст отказа или None."""
    from app import consistency, core, state, target
    if not tgt:
        return ("Атаки не запущены: стенд не задан — нет раздела stand в сценарии, адреса в ответе endpoint (Ш4) "
                "и адреса агента в проекте.")
    if any(c["id"] == "c:attack_on_prod" for c in consistency.check(st)):
        return "Атаки не запущены: место испытаний (attack_target, Ш5) похоже на рабочую систему, а не на стенд."
    rec = state.get_answer(st, core.qstep("attack_target"), "attack_target")
    where = str((rec or {}).get("value") or "")
    if not where or "стенд" not in where.lower():
        return "Атаки не запущены: в ответе attack_target (Ш5) не описан стенд — испытания идут только на стенде."
    host = urlparse(tgt["url"]).hostname or ""
    if not _loopback(host) and host not in where and target.host_label(tgt) not in where:
        return (f"Атаки не запущены: хост {host} не назван стендом в ответе attack_target (Ш5) — "
                "против адреса, который не описан как стенд, испытания не идут.")
    return None


def run_attacks(pid: str, sc: dict, typed_host: str, transport=None) -> dict:
    """Ветка атак механизмом приложения: предохранитель (arm) → пуск (check_launch) под замком, запросы вне замка,
    итоги (record) — снова под замком. Пишет адрес стенда в проект, находки — в риски и протокол."""
    from app import attacks, core, state
    with state.locked(pid):
        st = state.load(pid)
        tgt = stand_target(st, sc)
        why = stand_refusal(st, tgt)
        if why:
            return {"ran": False, "refusal": why}
        if st.get("target") != tgt:
            st["target"] = tgt
            st.pop("attack_arm", None)
        rec = state.get_answer(st, core.qstep("attack_selection"), "attack_selection")
        chosen = list(rec["value"]) if rec and rec["status"] in ("answered", "needs_detail") else []
        try:
            armed_plan = attacks.arm(st, chosen)  # первое подтверждение: --confirm-attacks-1
        except ValueError as e:
            return {"ran": False, "refusal": f"Атаки не запущены: {e}."}
        state.save(st, "испытания: предохранитель снят (сценарий)")
    print(f"План испытаний: {armed_plan['host']}, запросов {armed_plan['requests_total']}; "
          + ", ".join(a["title"] for a in armed_plan["attacks"]), flush=True)
    with state.locked(pid):
        st = state.load(pid)
        try:  # второе подтверждение: хост, набранный вручную в --confirm-attacks-2
            armed = attacks.check_launch(st, armed_plan["token"], typed_host, True)
        except PermissionError as e:
            state.save(st, "испытания: пуск отклонён (сценарий)")
            return {"ran": False, "refusal": f"Атаки не запущены: {e}."}
        state.save(st, "испытания: ключ пуска использован (сценарий)")
    run = attacks.execute(armed, transport)
    with state.locked(pid):
        st = state.load(pid)
        run = attacks.record(st, run)
        state.save(st, "испытания проведены (сценарий)")
    return {"ran": True, "target": run["target"], "protocol": Path(run["protocol"]).name,
            "summary": {r["id"]: r["verdict"] for r in run["results"]}}


# ---------- основной прогон ----------
def check_step(st: dict, step_id: int) -> list[str]:
    """Проблемы структуры шага без модели: незакрытые обязательные вопросы, мало строк, нет обязательной строки."""
    from app import core, readiness
    ss = readiness.step_status(st, core.step(step_id))
    out = [f"{core.code(step_id)}.{o['qid']}: {o['reason']}" + (f" ({o['detail']})" if o.get("detail") else "")
           for o in ss["open"]]
    if not ss["can_build"]:
        out.append(f"{core.code(step_id)}: документ шага не собирается")
    return out


def apply_review(st: dict, step_id: int, qid: str, rv: dict) -> None:
    """Вердикт Senior Pilot в проект — как это делает приложение: статус ответа, ступень наставника, замечания-риски
    (прежние замечания по этому вопросу заменяются новыми)."""
    from app import mentor, state
    state.set_senior_review(st, step_id, qid, rv)
    mentor.record(st, step_id, qid, rv)
    key = f"{step_id}.{qid}"
    state.close_risks(st, key, "senior")
    for rtext in rv.get("risks", []):
        state.add_risk(st, step_id, rtext, "caution", "senior", key=key)


def markup_review(a: dict) -> dict:
    """Вердикт по разметке сценария вместо модели (--dry-run): слабый ответ — needs_detail с заложенной ошибкой
    как подсказкой."""
    return {"verdict": "needs_detail", "comment": "вердикт по разметке сценария, без модели",
            "follow_up": f"добавьте: {(a.get('flaw') or {}).get('what') or 'конкретику'}", "follow_options": [],
            "sample": "", "synthesis": "", "stage": 1, "short_mode": False, "risks": [], "model": "разметка сценария",
            "cost_usd": 0}


def rework_step(st: dict, step_id: int, items: list[dict], rt, dry: bool) -> list[dict]:
    """Круг доработки шага: ответ получил needs_detail, в сценарии есть rework — доработанный ответ записывается
    и проверяется снова (с моделью — тем же senior.review_answer; в --dry-run — без модели, считается принятым)."""
    from app import senior, state
    todo = []
    for a in items:
        rec = state.get_answer(st, step_id, a["qid"])
        if a.get("rework") and rec and rec["status"] == "needs_detail":
            note = ((rec.get("senior") or {}).get("follow_up") or (rec.get("senior") or {}).get("comment") or "")
            state.set_answer(st, step_id, a["qid"], "answered", a["rework"], a.get("note", ""), a.get("extra"))
            todo.append((a, note))
    if not todo:
        return []
    snapshot = json.loads(json.dumps(st))

    def review(item):
        a, note = item
        if dry:
            return a, note, None, ""
        from app.router import RouterError
        try:
            return a, note, senior.review_answer(snapshot, step_id, a["qid"], router=rt), ""
        except RouterError as e:
            return a, note, None, str(e)

    out = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for a, note, rv, err in ex.map(review, todo):
            if rv:
                apply_review(st, step_id, a["qid"], rv)
            after = rv["verdict"] if rv else ("accepted" if dry else "ошибка")
            out.append({"step": step_id, "qid": a["qid"], "note": note, "rework": a["rework"], "after": after,
                        "follow_up": (rv or {}).get("follow_up", ""), "error": err,
                        "cost_usd": (rv or {}).get("cost_usd", 0), "model": (rv or {}).get("model", "")})
    return out


def rework_table(reworks: list[dict]) -> list[str]:
    """Раздел отчёта «Доработка после замечаний»."""
    from app import core
    L = ["## Доработка после замечаний", "",
         f"Доработано ответов: {len(reworks)}; принято после доработки: "
         f"{sum(1 for r in reworks if r['after'] == 'accepted')}; снова на доработку: "
         f"{sum(1 for r in reworks if r['after'] == 'needs_detail')}; ошибок вызова: "
         f"{sum(1 for r in reworks if r['after'] == 'ошибка')}. Стоимость повторных проверок: "
         f"{sum(r['cost_usd'] or 0 for r in reworks):.2f} $.", "",
         "| шаг | вопрос | замечание Senior Pilot | доработанный ответ | вердикт после доработки |", "|---|---|---|---|---|"]
    for r in reworks:
        verdict = {"accepted": "принято", "needs_detail": "снова нужно уточнить"}.get(r["after"], r["after"])
        if r["after"] == "needs_detail" and r["follow_up"]:
            verdict += ": " + r["follow_up"]
        if r["error"]:
            verdict += ": " + r["error"]
        L.append(f"| {core.code(r['step'])} | {core.question(r['step'], r['qid'])['text'][:120]} | "
                 + " | ".join(str(x).replace("|", "/").replace("\n", " ")[:600] for x in (r["note"] or "—", r["rework"], verdict))
                 + " |")
    return L + [""]


def run_rework_only(pid: str, sc: dict, name: str = "сценарий", dry: bool = False) -> dict:
    """--rework-only: проект уже прошёл платную проверку. Только круг доработки ответов со статусом needs_detail,
    у которых в сценарии есть rework, затем сборка документов всех шагов. Остальные ответы повторно не проверяются."""
    from app import artifacts, core, mentor, state
    rt = None
    if not dry:
        from app.router import Router
        rt = Router()
    answers, _ = to_route(sc)
    with state.locked(pid):
        st = state.load(pid)
    reworks, docs, blocked = [], [], {}
    for step_id in core.step_ids():
        items = [a for a in answers if a["step"] == step_id]
        for a in items:  # ступень наставника — как в приложении: первое «нужно уточнить» уже было
            rec = state.get_answer(st, step_id, a["qid"])
            if rec and rec["status"] == "needs_detail" and not mentor.guided(st, step_id, a["qid"]):
                mentor.record(st, step_id, a["qid"], {"verdict": "needs_detail"})
        reworks += rework_step(st, step_id, items, rt, dry)
        try:
            docs.append(artifacts.build_step(st, step_id)["path"])
        except ValueError as e:
            blocked[core.code(step_id)] = str(e)
    with state.locked(pid):
        fresh = state.load(pid)
        if fresh.get("updated") != st.get("updated"):
            raise RuntimeError("проект изменился во время доработки — повторите, ничего не записано")
        state.save(st, "сценарий: круг доработки слабых ответов" + (" (без модели)" if dry else ""))
    left = [f"{core.code(k)}.{q}" for k, v in st["answers"].items() for q, r in v.items()
            if r["status"] == "needs_detail" and not any(a["step"] == int(k) and a["qid"] == q and a.get("rework")
                                                         for a in answers)]
    out = {"project": pid, "mode": "dry-run" if dry else "модель", "reworked": len(reworks),
           "rework_accepted": sum(1 for r in reworks if r["after"] == "accepted"), "docs": len(docs),
           "blocked": blocked, "needs_detail_without_rework": left,
           "cost_reviews": round(sum(r["cost_usd"] or 0 for r in reworks), 3)}
    if not dry:
        now = datetime.now()
        L = [f"# Круг доработки «{st['name']}» — {now.strftime('%d.%m.%Y %H:%M')}", "",
             f"Проект {pid}. Повторных проверок Senior Pilot: {len(reworks)}, стоимость {out['cost_reviews']:.2f} $. "
             f"Документов шагов собрано: {len(docs)} из {len(core.step_ids())}.", ""]
        L += rework_table(reworks)
        if blocked:
            L += ["## Документы шагов, которые не собрались", ""] + [f"- **{k}.** {v}" for k, v in blocked.items()] + [""]
        if left:
            L += ["## Ответы «нужно уточнить» без доработки в сценарии", ""] + [f"- {x}" for x in left] + [""]
        f = HERE / f"доработка {Path(name).stem} {now.strftime('%d.%m.%Y %H-%M')}.md"
        f.write_text("\n".join(L), encoding="utf-8")
        out["report"] = f.name
    out["ok"] = not blocked
    return out


def run(args: argparse.Namespace, transport=None) -> dict:
    from app import artifacts, core, senior, state

    sc = load_scenario(args.scenario)
    p = sc["project"]
    dry = bool(args.dry_run)
    gate = attack_gate(args)
    if gate:
        print(gate, flush=True)
    rt = None
    if not dry:
        from app.router import Router
        rt = Router()
    st = state.create(p["name"], 1, p.get("description", ""), p.get("domain", ""))
    rows, debriefs, opts, problems, docs, reworks = [], {}, [], [], [], []
    answers, skipped = to_route(sc)

    for step_id in core.step_ids():
        items = [a for a in answers if a["step"] == step_id]
        for a in items:
            status = a.get("status", "answered")
            try:
                state.set_answer(st, step_id, a["qid"], status, a["value"] if status == "answered" else None,
                                 a.get("note", ""), a.get("extra"))
            except (KeyError, ValueError) as e:
                problems.append(f"{core.code(step_id)}.{a['qid']}: ответ не записан — {e}")
        snapshot = json.loads(json.dumps(st))

        def review(a):
            if dry:
                return a, None, "не проверялось: --dry-run"
            if a.get("status", "answered") != "answered":
                return a, None, "не проверялось: " + a["status"]
            from app.router import RouterError
            try:
                return a, senior.review_answer(snapshot, step_id, a["qid"], router=rt), ""
            except RouterError as e:
                return a, None, str(e)

        with ThreadPoolExecutor(max_workers=4) as ex:
            for a, rv, err in ex.map(review, items):
                if rv:
                    apply_review(st, step_id, a["qid"], rv)
                elif dry and a.get("rework") and norm_expect(a.get("expect")) == "needs_detail":
                    # без модели вердикт берётся из разметки сценария — чтобы круг доработки проходил и в --dry-run
                    apply_review(st, step_id, a["qid"], markup_review(a))
                rows.append({**a, "review": rv, "error": err})
        reworks += rework_step(st, step_id, items, rt, dry)
        if dry:
            problems += check_step(st, step_id)
        else:
            from app.router import RouterError
            try:
                debriefs[step_id] = senior.debrief_comment(st, step_id, router=rt)["comment"]
            except RouterError as e:
                debriefs[step_id] = f"ошибка: {e}"
        try:
            docs.append(artifacts.build_step(st, step_id)["path"])
        except ValueError as e:
            if not dry:
                debriefs[step_id] = (debriefs.get(step_id, "") + f" Документ шага не собран: {e}").strip()
        state.save(st, f"сценарий: шаг {core.code(step_id)}" + (" (без модели)" if dry else ""))

    if not dry:
        from app.router import RouterError
        for step_id, qid in sc.get("options_for", []):
            if not is_native(sc):
                m = core.migration().get("map", {}).get(f"{step_id}:{qid}")
                if not m:
                    continue
                step_id, qid = int(m.split(":")[0]), m.split(":")[1]
            try:
                o = senior.propose_options(st, int(step_id), qid, router=rt)
                opts.append({"step": int(step_id), "qid": qid, "options": o["options"]})
            except RouterError as e:
                opts.append({"step": int(step_id), "qid": qid, "error": str(e)})

    expected = {(s["id"], q["id"]) for s in core.steps() for q in s["questions"]}
    answered = {(int(k), q) for k, v in (st.get("answers") or {}).items() for q in v}
    summary = {"project": st["id"], "name": st["name"], "mode": "dry-run" if dry else "модель",
               "steps": len(core.step_ids()), "answers": len(answered & expected), "questions": len(expected),
               "docs": len(docs), "problems": problems, "skipped_old": len(skipped), "keep": bool(args.keep),
               "reworked": len(reworks), "rework_accepted": sum(1 for r in reworks if r["after"] != "needs_detail")}

    if dry:
        summary["ok"] = not problems and answered >= expected
    else:
        summary.update(report(args.scenario, sc, st, rows, debriefs, opts, skipped, reworks))
        summary["ok"] = not problems

    if args.attacks:
        if gate:
            summary["attacks"] = {"ran": False, "refusal": gate}
        else:
            res = run_attacks(st["id"], sc, str(args.confirm2).strip(), transport=transport)
            if not res["ran"]:
                print(res["refusal"], flush=True)
            summary["attacks"] = res
    return summary


def report(name: str, sc: dict, st: dict, rows: list, debriefs: dict, opts: list, skipped: list,
           reworks: list | None = None) -> dict:
    """Отчёт платного прогона: вердикты Senior Pilot против разметки, пойманные ошибки, противоречия, защита."""
    from app import consistency, core, defense, readiness
    p = sc["project"]
    risks = readiness.risks(st)
    cons = consistency.check(st)
    dfn = defense.for_project(st)
    ov = readiness.overall(st)

    graded = []
    for a in rows:
        rv = a["review"]
        got = rv["verdict"] if rv else ("—" if a["error"].startswith("не проверялось") else "ошибка")
        exp = norm_expect(a.get("expect"))
        verdict_ok = (got == exp) if rv else None
        flaw = a.get("flaw")
        caught, how = None, ""
        if flaw:
            sig = flaw["signal"]
            q_risk = next((r for r in risks if r["id"] == f"q:{a['step']}:{a['qid']}"), None)
            cons_hit = [c for c in cons if c["step"] == a["step"] or a["qid"] in c["text"]]
            senior_mention = bool(rv and (rv["verdict"] == "needs_detail" or rv.get("risks")))
            if sig == "risk":
                caught = bool(q_risk)
                how = (q_risk["reason"] + ", " + ("важно" if q_risk["level"] == "warning" else "на развитие")) if q_risk else ""
            elif sig == "consistency":
                hit = [c for c in cons if flaw["what"].split()[0].lower() in c["text"].lower()] or cons_hit
                caught = bool(hit) or senior_mention
                how = "; ".join(f"{c['id']} ({'важно' if c['level'] == 'warning' else 'на развитие'})" for c in hit[:2]) \
                    or ("замечание Senior Pilot" if senior_mention else "")
            else:
                # пойман — только отправка на доработку; принят с замечанием-риском — «частично» и в итог не идёт
                # (до 11.10.2026 принятый ответ с риском засчитывался как пойманный — прибор завышал результат)
                if rv and rv["verdict"] == "needs_detail":
                    caught = True
                elif rv and rv.get("risks"):
                    caught = "partial"
                else:
                    caught = False
                how = ("Senior Pilot: " + got) if rv else ""
        graded.append({**a, "expect_n": exp, "got": got, "verdict_ok": verdict_ok, "caught": caught, "how": how})

    rev = [g for g in graded if g["verdict_ok"] is not None]
    flaws = [g for g in graded if g.get("flaw")]
    cost = sum((g["review"] or {}).get("cost_usd", 0) for g in graded)
    now = datetime.now()
    dom = core.domain(p.get("domain"))
    L = [f"# Прогон цикла «{p['name']}» — {now.strftime('%d.%m.%Y %H:%M')}", "",
         f"Отрасль: {dom['title'] if dom else '—'}. Маршрут: {len(core.step_ids())} шагов, сценарий "
         f"{'нативный (schema 3)' if is_native(sc) else 'прежнего маршрута, переведён по таблице ядра'}. Модель Senior Pilot: "
         f"{next((g['review']['model'] for g in graded if g['review']), '—')}. Стоимость проверок ответов: {cost:.2f} $.", "",
         "## Итог", "",
         f"- Вердикты Senior Pilot совпали с разметкой: {sum(1 for g in rev if g['verdict_ok'])} из {len(rev)}.",
         f"- Лишняя строгость (хороший ответ отправлен на доработку): {sum(1 for g in rev if not g['verdict_ok'] and g['expect_n'] == 'accepted')}.",
         f"- Лишняя мягкость (намеренно слабый ответ принят): {sum(1 for g in rev if not g['verdict_ok'] and g['expect_n'] == 'needs_detail')}.",
         f"- Заложенных ошибок поймано: {sum(1 for g in flaws if g['caught'] is True)} из {len(flaws)}"
         f"{'; частично (принято с замечанием): ' + str(sum(1 for g in flaws if g['caught'] == 'partial')) if any(g['caught'] == 'partial' for g in flaws) else ''}.",
         f"- Ошибок вызова: {sum(1 for g in graded if g['got'] == 'ошибка')}.",
         f"- Готовность к проду: {ov['prod_readiness_pct']} %; важно до запуска — {ov['warnings']}, на развитие — {ov['cautions']}.",
         f"- Решение по защите: {dfn['go']['text']}", "",
         "## Заложенные ошибки — поймала ли система и с какой критичностью", "",
         "| шаг | вопрос | ошибка | ожидаемый сигнал | поймано | как |", "|---|---|---|---|---|---|"]
    for g in flaws:
        L.append(f"| {core.code(g['step'])} | {g['qid']} | {g['flaw']['what']} | {g['flaw']['signal']} | "
                 f"{'да' if g['caught'] is True else 'частично' if g['caught'] == 'partial' else 'нет'} | {g['how'] or '—'} |")
    L += ["", "## Противоречия между шагами", ""]
    L += [f"- {'важно' if c['level'] == 'warning' else 'на развитие'} · {core.code(c['step'])} · {c['text']}" for c in cons] or ["- нет"]
    L += ["", "## Ответы и рекомендации Senior Pilot", "",
          "| шаг | вопрос | разметка | вердикт | совпало | рекомендация Senior Pilot |", "|---|---|---|---|---|---|"]
    for g in graded:
        rv = g["review"]
        rec = ((rv["comment"] + (" Подсказка: " + rv["follow_up"] if rv.get("follow_up") else "")
                + ("" if not rv.get("risks") else " Рекомендации: " + " / ".join(rv["risks"]))) if rv else g["error"])
        L.append(f"| {core.code(g['step'])} | {g['qid']} | {g['expect_n']} | {g['got']} | "
                 f"{'—' if g['verdict_ok'] is None else ('да' if g['verdict_ok'] else 'нет')} | "
                 f"{rec.replace('|', '/').replace(chr(10), ' ')[:700]} |")
    L += [""] + rework_table(reworks or [])
    L += ["## Итоги шагов от Senior Pilot", ""]
    for k in core.step_ids():
        L += [f"**{core.code(k)}.** {debriefs.get(k, '')}", ""]
    if skipped:
        L += ["## Ответы сценария, не перенесённые на маршрут из 6 шагов", "",
              "Вопросы слиты в другие — перепишите сценарий под новые вопросы.", ""]
        L += [f"- {a['step']}:{a['qid']}" for a in skipped] + [""]
    L += ["## Варианты от Senior Pilot", ""]
    for o in opts:
        L.append(f"**{core.code(o['step'])} · {o['qid']}**")
        for x in o.get("options", []):
            L.append(f"- {x.get('title')}: {str(x.get('answer'))[:400]}")
        if o.get("error"):
            L.append(f"- ошибка: {o['error']}")
        L.append("")
    L += ["## Каркас защиты", "", dfn["markdown"]]
    out = HERE / f"прогон {Path(name).stem} {now.strftime('%d.%m.%Y %H-%M')}.md"
    out.write_text("\n".join(L), encoding="utf-8")
    cost += sum(r["cost_usd"] or 0 for r in reworks or [])
    return {"report": out.name, "verdicts_ok": sum(1 for g in rev if g["verdict_ok"]), "reviewed": len(rev),
            "flaws_caught": sum(1 for g in flaws if g["caught"] is True), "flaws": len(flaws),
            "errors": sum(1 for g in graded if g["got"] == "ошибка"), "cost_reviews": round(cost, 3),
            "go": dfn["go"]["verdict"]}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from app import config, state
    if not args.dry_run or (args.attacks and not attack_gate(args)):
        config.load_env_local()  # ключ роутера и токен агента (AGENT_*); значения не печатаются
    if args.rework_only:  # проект человека — в архив не уходит, новый проект не создаётся
        try:
            summary = run_rework_only(args.rework_only, load_scenario(args.scenario), args.scenario, bool(args.dry_run))
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            summary = {"ok": False, "error": str(e)}
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if summary.get("ok") else 1
    if args.keep:
        summary = run(args)
    else:
        with state.archived_test_run():  # проект прогона не остаётся в общем списке; документы шагов — в архиве
            summary = run(args)
    print(json.dumps(summary, ensure_ascii=False))
    if not summary.get("ok"):
        return 1
    if args.attacks and not (summary.get("attacks") or {}).get("ran"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
