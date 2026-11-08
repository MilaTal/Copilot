"""Командная строка — второй вход в то же ядро. Через неё Claude в чате ведёт проект как Senior Pilot.

  python -m app.cli new "Название" [--start N]
  python -m app.cli list
  python -m app.cli status <pid>
  python -m app.cli questions <pid> <шаг>
  python -m app.cli answer <pid> <шаг> <вопрос> --text "..." | --rows-json '[[...],[...]]' | --unknown | --skip [--note "..."]
  python -m app.cli risks <pid>                      — риски с идентификаторами
  python -m app.cli risk-owner <pid> <id риска> --owner "роль"
  python -m app.cli debrief <pid> <шаг>
  python -m app.cli build <pid> <шаг> | passport <pid> | prototype <pid> | defense <pid>
  python -m app.cli workspace <pid> --path "D:\\work\\agent" [--cloud]
  python -m app.cli scan <pid>
  python -m app.cli target <pid> --url http://127.0.0.1:8100/run
  python -m app.cli attack-arm <pid> --attacks injection,cost,excessive
  python -m app.cli attack-launch <pid> --token T --host 127.0.0.1:8100 --not-prod
Ответы в чате идут без проверки моделью роутера: Senior Pilot здесь — сам Claude."""
from __future__ import annotations

import argparse
import json
import sys

from . import artifacts, attacks, config, core, defense, prototypes, readiness, recommend, state, target, workspace


def _p(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def main(argv=None):
    config.load_env_local()
    for поток in (sys.stdout, sys.stderr):
        if hasattr(поток, "reconfigure"):
            поток.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="copilot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("new"); a.add_argument("name"); a.add_argument("--start", type=int, default=1, help=f"шаг старта, 1–{core.step_count()}"); a.add_argument("--desc", default="")
    a.add_argument("--domain", default="", help="отрасль: " + ", ".join(d["id"] for d in core.domains()))
    sub.add_parser("domains")
    sub.add_parser("list")
    for name in ("status", "passport", "prototype", "defense", "scan"):
        sub.add_parser(name).add_argument("pid")
    for name in ("questions", "build", "debrief"):
        b = sub.add_parser(name); b.add_argument("pid"); b.add_argument("step", type=int)
    c = sub.add_parser("answer"); c.add_argument("pid"); c.add_argument("step", type=int); c.add_argument("qid")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--text"); g.add_argument("--rows-json"); g.add_argument("--unknown", action="store_true"); g.add_argument("--skip", action="store_true")
    c.add_argument("--note", default="")
    c.add_argument("--owner", default="", help="владелец риска (роль) — для --unknown, --skip и рискованных вариантов")
    sub.add_parser("risks").add_argument("pid")
    sub.add_parser("recs").add_argument("pid")
    rc = sub.add_parser("rec"); rc.add_argument("pid"); rc.add_argument("rec_id")
    rc.add_argument("--action", required=True, choices=["start", "done", "defer", "dismiss", "reopen"])
    rc.add_argument("--reason", default=""); rc.add_argument("--owner", default="")
    ro = sub.add_parser("risk-owner"); ro.add_argument("pid"); ro.add_argument("risk_id"); ro.add_argument("--owner", required=True)
    w = sub.add_parser("workspace"); w.add_argument("pid"); w.add_argument("--path", required=True); w.add_argument("--cloud", action="store_true")
    t = sub.add_parser("target"); t.add_argument("pid"); t.add_argument("--url", required=True); t.add_argument("--method", default="POST")
    t.add_argument("--input-field", default="query"); t.add_argument("--output-field", default="answer")
    ar = sub.add_parser("attack-arm"); ar.add_argument("pid")
    ar.add_argument("--attacks", default="", help="через запятую; по умолчанию — выбранные в ответе о выборе атак (attack_selection)")
    al = sub.add_parser("attack-launch"); al.add_argument("pid"); al.add_argument("--token", required=True)
    al.add_argument("--host", required=True); al.add_argument("--not-prod", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "new":
        st = state.create(args.name, args.start, args.desc, args.domain); print(st["id"]); return
    if args.cmd == "domains":
        _p([{"id": d["id"], "title": d["title"], "summary": d.get("summary", "")} for d in core.domains()]); return
    if args.cmd == "list":
        _p([{k: p[k] for k in ("id", "name", "updated_text", "current_step") if k in p} for p in state.list_projects()])
        return
    if args.cmd == "attack-launch":
        # ключ пуска гасится под замком, запросы к агенту — вне замка, итоги — снова под замком
        with state.locked(args.pid):
            st = state.load(args.pid)
            try:
                armed = attacks.check_launch(st, args.token, args.host, args.not_prod)
            except PermissionError as e:
                print(f"ОТКЛОНЕНО: {e}"); sys.exit(2)
            state.save(st, "испытания: ключ пуска использован (чат)")
        run = attacks.execute(armed)
        with state.locked(args.pid):
            st = state.load(args.pid)
            run = attacks.record(st, run)
            state.save(st, "испытания проведены (чат)")
        _p({"protocol": run["protocol"], "summary": {r["id"]: r["verdict"] for r in run["results"]}})
        return
    with state.locked(args.pid):
        _command(args)


def _command(args):
    st = state.load(args.pid)
    if args.cmd == "status":
        _p({"overall": readiness.overall(st),
            "steps": [{k: s[k] for k in ("code", "title", "label", "closed", "required", "full", "artifact")} for s in readiness.all_status(st)],
            "risks": [{k: r[k] for k in ("code", "level", "reason", "text", "owner")} for r in readiness.risks(st)]})
    elif args.cmd == "questions":
        s = core.step(args.step)
        out = {"code": s["code"], "title": s["title"], "goal": s["goal"], "ready": s["ready"], "questions": [],
               "domain_examples": core.domain_examples(st.get("domain"), args.step)}
        for q in s["questions"]:
            rec = state.get_answer(st, args.step, q["id"])
            out["questions"].append({"id": q["id"], "type": q["type"], "text": q["text"], "required": q["required"],
                                     "why": q["why"], "hint": q["hint"], "example": q["example"],
                                     "columns": q.get("columns"), "min_rows": q.get("min_rows"), "options": q.get("options"),
                                     "status": rec["status"] if rec else "missing", "value": rec["value"] if rec else None})
        _p(out)
    elif args.cmd == "answer":
        if args.unknown:
            state.set_answer(st, args.step, args.qid, "unknown", None, args.note)
        elif args.skip:
            state.set_answer(st, args.step, args.qid, "skipped", None, args.note)
        elif args.rows_json:
            state.set_answer(st, args.step, args.qid, "answered", json.loads(args.rows_json), args.note)
        else:
            q = core.question(args.step, args.qid)
            val = [v.strip() for v in args.text.split(";")] if q["type"] == "multi" else args.text
            state.set_answer(st, args.step, args.qid, "answered", val, args.note)
        if args.owner:
            st.setdefault("risk_owners", {})[f"q:{args.step}:{args.qid}"] = args.owner.strip()[:120]
        state.save(st, f"ответ {core.step(args.step)['code']}.{args.qid} (чат)")
        ss = readiness.step_status(st, core.step(args.step))
        _p({k: ss[k] for k in ("code", "label", "closed", "required", "full", "open")})
    elif args.cmd == "risks":
        _p([{k: r[k] for k in ("id", "code", "level", "reason", "text", "owner")} for r in readiness.risks(st)])
    elif args.cmd == "recs":
        v = recommend.view(st)
        state.save(st, "рекомендации (чат)")
        _p({"next": [{k: i[k] for k in ("id", "code", "title", "why", "how", "status", "score")} for i in v["next"]],
            "counts": v["counts"]})
    elif args.cmd == "rec":
        res = recommend.act(st, args.rec_id, args.action, args.reason, args.owner)
        state.save(st, f"рекомендация {args.rec_id}: {args.action} (чат)")
        _p(res)
    elif args.cmd == "risk-owner":
        if args.risk_id not in {r["id"] for r in readiness.risks(st)}:
            raise ValueError(f"нет открытого риска {args.risk_id} — список: python -m app.cli risks {args.pid}")
        st.setdefault("risk_owners", {})[args.risk_id] = args.owner.strip()[:120]
        state.save(st, f"владелец риска {args.risk_id} (чат)")
        print(f"владелец риска {args.risk_id}: {args.owner}")
    elif args.cmd == "debrief":
        _p(readiness.step_debrief(st, args.step))
    elif args.cmd == "build":
        res = artifacts.build_step(st, args.step); state.save(st, f"документ {core.step(args.step)['code']} (чат)"); print(res["path"])
    elif args.cmd == "passport":
        print(artifacts.passport(st)["path"])
    elif args.cmd == "prototype":
        res = prototypes.build_all(st); state.save(st, "прототип (чат)")
        _p({"folder": res["folder"], "pattern": res["diagram"]["pattern"], "missing": res["diagram"]["missing"],
            "python_files": res["python_files"]}); print(res["diagram"]["mermaid"])
    elif args.cmd == "defense":
        d = defense.for_project(st); print(d["path"]); _p(d["go"])
    elif args.cmd == "workspace":
        p = workspace.validate_path(args.path)
        st["workspace"] = {"path": str(p), "cloud_allowed": bool(args.cloud)}
        state.save(st, "рабочая папка (чат)"); print(f"подключено: {p}; облачной модели: {'можно' if args.cloud else 'нельзя'}")
    elif args.cmd == "scan":
        rep = workspace.scan(st); state.save(st, "проверка разработки (чат)")
        _p({k: rep[k] for k in ("files_total", "files_read", "git", "diff", "findings")})
    elif args.cmd == "target":
        st["target"] = target.normalize({"url": args.url, "method": args.method, "input_field": args.input_field,
                                         "output_field": args.output_field})
        st.pop("attack_arm", None); state.save(st, "адрес агента (чат)"); _p(st["target"])
    elif args.cmd == "attack-arm":
        chosen = [x.strip() for x in args.attacks.split(",") if x.strip()]
        if not chosen:
            rec = state.get_answer(st, core.qstep("attack_selection"), "attack_selection")
            chosen = rec["value"] if rec and rec["status"] == "answered" else []
        res = attacks.arm(st, chosen)
        state.save(st, "испытания: предохранитель снят (чат)"); _p(res)


def run():
    try:
        main()
    except (ValueError, FileNotFoundError, KeyError, PermissionError, TimeoutError) as e:
        print(f"ОШИБКА: {str(e).strip(chr(39))}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    run()
