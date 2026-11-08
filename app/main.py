"""Copilot по проектированию ИИ-автоматизации — локальный сервер.

Запуск: run_dev.ps1 (читает .env.local, слушает только 127.0.0.1)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import (artifacts, attacks, config, core, defense, evalrun, mentor, prototypes, readiness, recommend, senior,
               state,
               target, workspace)
from .router import (ModelsConfigError, Router, RouterError, is_local_url, load_models, public_view, recent_calls,
                     save_models)

config.load_env_local()
app = FastAPI(title="Copilot по проектированию ИИ-автоматизации")
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.middleware("http")
async def no_cache_static(request, call_next):
    """Экран обновляется вместе с copilot: браузер не держит старые стили и скрипты."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.exception_handler(Exception)
async def unexpected(request: Request, exc: Exception):
    """Непредвиденная ошибка — понятный текст без путей и трассировки."""
    return JSONResponse({"detail": "Внутренняя ошибка copilot. Действие не выполнено; повторите или сообщите сопровождению."},
                        status_code=500)


def _load(pid: str) -> dict:
    try:
        return state.load(pid)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, str(e))


def _int(body: dict, key: str, lo: int = 1, hi: int | None = None) -> int:
    """Номер шага: от 1 до числа шагов в ядре."""
    hi = core.step_count() if hi is None else hi
    try:
        v = int(body.get(key))
    except (TypeError, ValueError):
        raise HTTPException(400, f"поле {key} — целое число от {lo} до {hi}")
    if not lo <= v <= hi:
        raise HTTPException(400, f"поле {key} — целое число от {lo} до {hi}")
    return v


def _step_ok(step_id: int) -> None:
    if not core.has_step(step_id):
        raise HTTPException(404, f"нет такого шага маршрута — шаги с 1 по {core.step_count()}")


def _str(body: dict, key: str, default: str = "", limit: int = 20_000) -> str:
    v = body.get(key, default)
    if v is None:
        return default
    if not isinstance(v, (str, int, float)):
        raise HTTPException(400, f"поле {key} — строка")
    return str(v)[:limit]


@contextmanager
def _mutate(pid: str, reason: str):
    """Прочитать — изменить — сохранить под замком данных проекта. Исключение внутри — ничего не сохраняется."""
    try:
        with state.locked(pid):
            st = _load(pid)
            yield st
            state.save(st, reason)
    except TimeoutError as e:
        raise HTTPException(409, str(e))
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, str(e))


def _view(st: dict) -> dict:
    return {"project": st, "steps": readiness.all_status(st), "overall": readiness.overall(st),
            "risks": readiness.risks(st), "annunciator": readiness.annunciator(st)}


def _router_error(e: Exception):
    return JSONResponse({"error": str(e), "senior_unavailable": True}, status_code=503)


@app.get("/")
def index():
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/api/meta")
def meta():
    return {"phases": core.phases(), "steps": core.steps(), "patterns": core.patterns(), "hints": core.hints(),
            "attacks": core.attacks(), "status_labels": core.STATUS_LABELS, "domains": core.domains(),
            "levels": core.levels()}


# ---------- проекты ----------
@app.get("/api/projects")
def projects():
    out = state.list_projects()
    # «маршрут пройден» — по закрытым шагам, а не по последнему открытому: карточка не должна показывать
    # «шаг Ш4» у проекта, где все шесть шагов закрыты
    for p in out:
        if p.get("broken"):
            continue
        try:
            st = state.load(p["id"])
            ss = [readiness.step_status(st, s) for s in core.steps()]
            p["route_done"] = all(x["can_build"] for x in ss)
            p["steps_closed"] = sum(1 for x in ss if x["can_build"])
            p["steps_total"] = len(ss)
        except Exception:  # noqa: BLE001 — список не должен падать из-за одного проекта
            p["route_done"] = False
    return out


@app.post("/api/projects")
def create_project(body: dict = Body(...)):
    try:
        st = state.create(body.get("name", ""), body.get("start_step") or 1, _str(body, "description"),
                          _str(body, "domain", limit=40))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _view(st)


@app.delete("/api/projects/{pid}")
def archive_project(pid: str):
    """В архив, не в корзину: папка проекта переносится и может быть возвращена вручную."""
    try:
        where = state.archive_project(pid)
    except FileNotFoundError:
        raise HTTPException(404, "проект не найден")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"archived": where}


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return _view(_load(pid))


@app.get("/api/projects/{pid}/versions")
def get_versions(pid: str):
    _load(pid)
    return state.versions(pid)


@app.post("/api/projects/{pid}/restore")
def restore(pid: str, body: dict = Body(...)):
    try:
        with state.locked(pid):
            return _view(state.restore(pid, body.get("version", "")))
    except (ValueError, FileNotFoundError, json.JSONDecodeError):
        raise HTTPException(400, "версия не найдена или повреждена")


@app.post("/api/projects/{pid}/answer")
def answer(pid: str, body: dict = Body(...)):
    """Ответ сохраняется сразу под замком; проверка Senior Pilot идёт вне замка и вливается, только если ответ не меняли."""
    step_id, qid = _int(body, "step"), _str(body, "qid", limit=64)
    status = _str(body, "status", "answered", 20)
    with _mutate(pid, f"ответ {core.code(step_id)}.{qid}") as st:
        try:
            rec = state.set_answer(st, step_id, qid, status, body.get("value"), _str(body, "note"),
                                   _str(body, "extra", limit=4000) if "extra" in body else None)
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e).strip("'"))
        snapshot = json.loads(json.dumps(st))
    review, review_error = None, None
    if body.get("review", True) and status == "answered":
        try:
            review = senior.review_answer(snapshot, step_id, qid)
        except RouterError as e:
            review_error = str(e)
        except Exception:  # noqa: BLE001 — ответ уже сохранён; сбой проверки не должен превращаться в 500
            review_error = "проверка Senior Pilot не выполнена из-за внутренней ошибки — ответ сохранён"
    if review or review_error:
        with _mutate(pid, f"проверка Senior Pilot {core.code(step_id)}.{qid}") as st:
            cur = state.get_answer(st, step_id, qid)
            same = bool(cur) and cur["updated"] == rec["updated"] and cur["value"] == rec["value"]
            if review and same:
                state.set_senior_review(st, step_id, qid, review)
                mentor.record(st, step_id, qid, review)
                qkey = f"{step_id}.{qid}"
                state.close_risks(st, qkey, "senior")  # прежние замечания по этому вопросу заменяются новыми
                for rtext in review.get("risks", []):
                    state.add_risk(st, step_id, rtext, "caution", "senior", key=qkey)
            elif review and not same:
                review = None
                review_error = "ответ изменили, пока Senior Pilot его читал — проверка относится к прежней версии и не применена"
                state.set_senior_error(st, step_id, qid, review_error)
            elif review_error and same:
                # сбой проверки тоже остаётся в ответе: после перезагрузки видно, что проверки не было
                state.set_senior_error(st, step_id, qid, review_error)
    out = _view(_load(pid))
    out["review"] = review
    out["review_error"] = review_error
    return out

# ---------- наставник: формулировка ответа, вывод шага, предзаполнение ----------
@app.post("/api/projects/{pid}/answer-synthesis")
def answer_synthesis(pid: str, body: dict = Body(...)):
    step_id = _int(body, "step")
    with _mutate(pid, f"формулировка ответа {core.code(step_id)}") as st:
        try:
            out = mentor.confirm_answer_synthesis(st, step_id, _str(body, "qid", limit=64), _str(body, "text", limit=1500))
        except KeyError:
            raise HTTPException(400, "нет такого вопроса")
        except ValueError as e:
            raise HTTPException(400, str(e))
    return out


@app.get("/api/projects/{pid}/synthesis/{step_id}")
def synthesis_get(pid: str, step_id: int):
    _step_ok(step_id)
    return mentor.step_synthesis_draft(_load(pid), step_id)


@app.post("/api/projects/{pid}/synthesis/{step_id}")
def synthesis_save(pid: str, step_id: int, body: dict = Body(...)):
    _step_ok(step_id)
    with _mutate(pid, f"вывод шага {core.code(step_id)} подтверждён") as st:
        try:
            return mentor.save_step_synthesis(st, step_id, _str(body, "text", limit=4000))
        except ValueError as e:
            raise HTTPException(400, str(e))


@app.get("/api/projects/{pid}/prefill")
def prefill(pid: str, step: int, qid: str):
    _step_ok(step)
    try:
        st = _load(pid)
        return {"rows": mentor.prefill(st, step, qid[:64]), "text": mentor.prefill_text(st, step, qid[:64])}
    except KeyError:
        raise HTTPException(400, "нет такого вопроса")


@app.post("/api/projects/{pid}/options")
def options(pid: str, body: dict = Body(...)):
    st = _load(pid)
    try:
        return senior.propose_options(st, _int(body, "step"), _str(body, "qid", limit=64))
    except KeyError:
        raise HTTPException(400, "нет такого вопроса")
    except RouterError as e:
        return _router_error(e)


@app.post("/api/projects/{pid}/ask")
def ask(pid: str, body: dict = Body(...)):
    st = _load(pid)
    try:
        return senior.ask(st, _int(body, "step"), _str(body, "message", limit=4000))
    except RouterError as e:
        return _router_error(e)


@app.get("/api/projects/{pid}/debrief/{step_id}")
def debrief(pid: str, step_id: int, comment: bool = False):
    _step_ok(step_id)
    st = _load(pid)
    d = readiness.step_debrief(st, step_id)
    if comment:
        try:
            d["senior"] = senior.debrief_comment(st, step_id)
        except RouterError as e:
            d["senior_error"] = str(e)
    return d


@app.post("/api/projects/{pid}/domain")
def set_domain(pid: str, body: dict = Body(...)):
    dom = _str(body, "domain", limit=40)
    if dom and not core.domain(dom):
        raise HTTPException(400, "нет такой отрасли")
    with _mutate(pid, "отрасль проекта") as st:
        st["domain"] = dom
    return _view(_load(pid))


# ---------- рекомендации Senior Pilot ----------
@app.get("/api/projects/{pid}/recs")
def recs_view(pid: str):
    """Сохраняем, только если план изменился (автозакрытие, новые рекомендации) — чтобы не плодить версии."""
    try:
        with state.locked(pid):
            st = _load(pid)
            before = json.dumps(st.get("recs") or {}, ensure_ascii=False, sort_keys=True)
            out = recommend.view(st)
            if json.dumps(st.get("recs") or {}, ensure_ascii=False, sort_keys=True) != before:
                state.save(st, "рекомендации: обновлён план")
    except TimeoutError as e:
        raise HTTPException(409, str(e))
    return out


@app.post("/api/projects/{pid}/recs/{rid}")
def recs_act(pid: str, rid: str, body: dict = Body(...)):
    with _mutate(pid, f"рекомендация {rid}: {_str(body, 'action', limit=20)}") as st:
        try:
            res = recommend.act(st, rid, _str(body, "action", limit=20), _str(body, "reason", limit=300),
                                _str(body, "owner", limit=120))
        except ValueError as e:
            raise HTTPException(400, str(e))
        res["recs"] = recommend.view(st)
    return res


@app.post("/api/projects/{pid}/recs/{rid}/enrich")
def recs_enrich(pid: str, rid: str):
    """План от Senior Pilot: вызов модели вне замка, результат — под замком."""
    snap = _load(pid)
    try:
        res = recommend.enrich(snap, rid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RouterError as e:
        return _router_error(e)
    with _mutate(pid, f"рекомендация {rid}: план от Senior Pilot") as st:
        st.setdefault("recs", {}).setdefault(rid, {"status": "new"})["enriched"] = snap["recs"][rid]["enriched"]
    return res


@app.post("/api/projects/{pid}/risk")
def risk_update(pid: str, body: dict = Body(...)):
    rid = _str(body, "id", limit=80)
    add_text = _str(body, "add", limit=600)
    add_step = _int(body, "step") if add_text else 1
    with _mutate(pid, "риски") as st:
        if body.get("owner") is not None:
            st.setdefault("risk_owners", {})[rid] = _str(body, "owner", limit=120).strip()
        if body.get("close"):
            for r in st.get("risks_extra", []):
                if r["id"] == rid:
                    r["status"] = "closed"
        if add_text:
            state.add_risk(st, add_step, add_text, _str(body, "level", "caution", 10), "manual", _str(body, "owner", limit=120))
    return _view(_load(pid))


# ---------- документы ----------
@app.post("/api/projects/{pid}/build/{step_id}")
def build(pid: str, step_id: int):
    _step_ok(step_id)
    with _mutate(pid, f"документ {core.code(step_id)}") as st:
        try:
            res = artifacts.build_step(st, step_id)
        except ValueError as e:
            raise HTTPException(409, str(e))
    return res


@app.post("/api/projects/{pid}/passport")
def passport(pid: str):
    st = _load(pid)
    return artifacts.passport(st)


@app.get("/api/projects/{pid}/docs")
def docs(pid: str):
    return artifacts.list_docs(_load(pid))


@app.get("/api/projects/{pid}/docs/{name}")
def doc(pid: str, name: str):
    try:
        return {"name": name, "markdown": artifacts.read_doc(_load(pid), name)}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


@app.get("/api/projects/{pid}/docs/{name}/download")
def doc_download(pid: str, name: str):
    """Документ шага отдаётся файлом: без кнопки «Скачать» результат было нечем забрать из интерфейса."""
    from urllib.parse import quote
    try:
        text = artifacts.read_doc(_load(pid), name)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    safe = name if name.lower().endswith(".md") else name + ".md"
    return Response(content=text, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(safe)}"})


# ---------- прототипы ----------
@app.get("/api/projects/{pid}/diagram")
def diagram(pid: str):
    return prototypes.agent_diagram(_load(pid))


@app.post("/api/projects/{pid}/prototype")
def prototype(pid: str):
    with _mutate(pid, "прототип собран") as st:
        res = prototypes.build_all(st)
    return res


# ---------- рабочая папка Junior Pilot ----------
@app.put("/api/projects/{pid}/workspace")
def set_workspace(pid: str, body: dict = Body(...)):
    try:
        path = workspace.validate_path(body.get("path", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    with _mutate(pid, "рабочая папка подключена") as st:
        ws = st.get("workspace") or {}
        if ws.get("path") != str(path):
            ws = {"path": str(path)}
        ws["cloud_allowed"] = bool(body.get("cloud_allowed", False))
        st["workspace"] = ws
    return {"workspace": {k: v for k, v in ws.items() if k != "last_inventory"}}


@app.post("/api/projects/{pid}/workspace/scan")
def scan_workspace(pid: str):
    snap = _load(pid)
    try:
        rep = workspace.scan(snap)  # долгая опись файлов — вне замка проекта
    except ValueError as e:
        raise HTTPException(400, str(e))
    with _mutate(pid, "проверка разработки кодом") as st:
        st.setdefault("workspace", {}).update({"last_scan": snap["workspace"]["last_scan"]})
        st["workspace"].pop("last_inventory", None)
        st["risks_extra"] = [r for r in st.get("risks_extra", []) if r.get("source") != "workspace" or r.get("status") != "open"]
        for f in rep["findings"]:
            if f["level"] in ("warning", "caution"):
                state.add_risk(st, f["step"], f"Разработка: {f['text']}" + (f" ({f['file']})" if f.get("file") else ""),
                               f["level"], "workspace")
    return rep


@app.get("/api/projects/{pid}/workspace/preview")
def preview_material(pid: str):
    """Что именно уйдёт модели: файлы, объём, сколько секретов замаскировано, облако или только локально."""
    st = _load(pid)
    try:
        m = workspace.material_for_model(st)
    except ValueError as e:
        raise HTTPException(400, str(e))
    cfg = load_models()
    names = cfg["roles"].get("reviewer", [])
    allowed = [n for n in names if m["cloud_allowed"] or is_local_url(cfg["endpoints"].get(n, {}).get("base_url", ""))]
    return {"files": m["files"], "chars": m["chars"], "masked": m["masked"], "cloud_allowed": m["cloud_allowed"],
            "endpoints": allowed}


@app.post("/api/projects/{pid}/workspace/review")
def review_workspace(pid: str, body: dict = Body(...)):
    if not body.get("confirm"):
        raise HTTPException(400, "сначала посмотрите, что уйдёт модели, и подтвердите отправку")
    st = _load(pid)
    try:
        m = workspace.material_for_model(st)
        res = senior.review_material(st, m)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RouterError as e:
        return _router_error(e)
    with _mutate(pid, "разбор разработки моделью") as st:
        for r in res.get("risks") or []:
            try:
                state.add_risk(st, int(r["step"]) if core.has_step(r.get("step")) else core.qstep("prototype_run"),
                               f"Разбор разработки: {r.get('text', '')}",
                               r.get("level") if r.get("level") in ("warning", "caution") else "caution", "senior")
            except (KeyError, ValueError, TypeError):
                continue
        st.setdefault("workspace", {})["last_review"] = {k: res.get(k) for k in ("summary", "mismatches", "model", "endpoint")}
    return res


# ---------- агент Junior Pilot: проверки и испытания ----------
@app.put("/api/projects/{pid}/target")
def set_target(pid: str, body: dict = Body(...)):
    try:
        tgt = target.normalize(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with _mutate(pid, "адрес агента") as st:
        st["target"] = tgt
        st.pop("attack_arm", None)
    return {"target": tgt}


@app.post("/api/projects/{pid}/eval/run")
def eval_run(pid: str, body: dict = Body(...)):
    if not body.get("confirm"):
        raise HTTPException(400, "прогон набора — реальные запросы к агенту; нужно подтверждение")
    try:
        res = evalrun.execute(_load(pid))
    except ValueError as e:
        raise HTTPException(400, str(e))
    with _mutate(pid, "прогон набора проверок") as st:
        out = evalrun.record(st, res)
    return out


@app.get("/api/projects/{pid}/eval/regression")
def eval_regression(pid: str):
    return evalrun.regression(_load(pid)) or {}


@app.get("/api/projects/{pid}/cost-of-success")
def cost_of_success(pid: str):
    st = _load(pid)
    rec = state.get_answer(st, core.qstep("runs_table"), "runs_table")
    return evalrun.cost_of_success(rec["value"] if rec and rec["status"] == "answered" else [])


@app.post("/api/projects/{pid}/attacks/arm")
def attacks_arm(pid: str, body: dict = Body(...)):
    """Первое подтверждение: план испытаний и ключ пуска."""
    with _mutate(pid, "испытания: предохранитель снят") as st:
        try:
            res = attacks.arm(st, body.get("attacks") or [])
        except ValueError as e:
            raise HTTPException(400, str(e))
    return res


@app.post("/api/projects/{pid}/attacks/launch")
def attacks_launch(pid: str, body: dict = Body(...)):
    """Второе подтверждение под замком (ключ одноразовый), запросы к агенту — вне замка, итоги — снова под замком."""
    with _mutate(pid, "испытания: ключ пуска использован") as st:
        try:
            armed = attacks.check_launch(st, body.get("token", ""), body.get("typed_host", ""), bool(body.get("not_prod")))
        except PermissionError as e:
            raise HTTPException(403, str(e))
    run = attacks.execute(armed)
    with _mutate(pid, "испытания проведены") as st:
        res = attacks.record(st, run)
    return res


# ---------- защита ----------
@app.post("/api/projects/{pid}/defense")
def defense_project(pid: str):
    st = _load(pid)
    return defense.for_project(st)


@app.post("/api/defense/documents")
def defense_documents(body: dict = Body(...)):
    text = body.get("text", "")
    if not isinstance(text, str):
        raise HTTPException(400, "text — строка с текстом документов")
    if len(text.strip()) < 200:
        raise HTTPException(400, "вставьте текст документов проекта — хотя бы абзац с числами")
    try:
        return defense.from_documents(text, body.get("title") or "Проект", only_local=not body.get("cloud_allowed"))
    except RouterError as e:
        return _router_error(e)
    except ValueError as e:
        raise HTTPException(422, f"модель вернула не то: {e}")


# ---------- модели ----------
@app.get("/api/models")
def models_get():
    try:
        cfg = load_models()
    except ModelsConfigError as e:
        # экран открывается и при битом файле — чтобы его можно было починить
        try:
            raw = config.models_file().read_text(encoding="utf-8")[:20000]
            raw, _ = workspace.mask_secrets(raw)
        except OSError:
            raw = ""
        return {"error": str(e), "raw": raw, "file": config.models_file().name, "calls": recent_calls(30)}
    r = Router(cfg)
    return {"config": public_view(cfg), "budget": r.budget_state(), "calls": recent_calls(30),
            "file": config.models_file().name, "warnings": _price_warnings(cfg)}


def _price_warnings(cfg: dict) -> list[str]:
    """Нулевая цена у платной точки молча обнуляет учёт расходов — говорим об этом при сохранении."""
    out = []
    for name, ep in (cfg.get("endpoints") or {}).items():
        if not isinstance(ep, dict) or ep.get("kind") not in ("openai", "anthropic"):
            continue
        if is_local_url(str(ep.get("base_url") or "")):
            continue
        if not float(ep.get("price_in") or 0) and not float(ep.get("price_out") or 0):
            out.append(f"у точки «{name}» цена 0 — расход по ней считаться не будет; впишите цену за 1 млн токенов")
    return out


@app.put("/api/models")
def models_put(body: dict = Body(...)):
    cfg: dict[str, Any] = body.get("config") or {}
    if not isinstance(cfg, dict):
        raise HTTPException(400, "config — словарь настроек")
    for ep in (cfg.get("endpoints") or {}).values():
        if isinstance(ep, dict):
            ep.pop("key_present", None)
            ep.pop("key_needed", None)
    try:
        save_models(cfg)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return models_get()


@app.post("/api/models/check/{name}")
def models_check(name: str):
    try:
        return Router().check(name)
    except ModelsConfigError as e:
        return {"ok": False, "error": str(e)}
