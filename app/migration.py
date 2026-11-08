"""Переезд сохранённых проектов на текущую схему маршрута. Срабатывает сам при загрузке проекта.

Схема 1 → 2 — прежние переносы вопросов внутри маршрута из 12 шагов (таблица ниже — история, ядро её уже не знает).
Схема 2 → 3 — маршрут из 12 шагов становится маршрутом из 6. Таблица «старый шаг:id → новый шаг:id», правила
слияния и пересчёт номеров шагов читаются из ядра (core/steps.yaml, ключ migration) — в коде они не дублируются.

Ничего не теряется: слитый ответ кладётся в ответ-приёмник по правилу из ядра, а исходная запись целиком
сохраняется в его поле merged_from. Переезд идемпотентен: проект схемы 3 не трогается."""
from __future__ import annotations

import copy
import re

from . import core
from .state import now

SCHEMA = 3

# Схема 1 → 2: прежние переносы и слияния (история; позиции старого маршрута из 12 шагов).
_S1_MOVES = [("origin", 1, 2), ("data_where", 1, 2), ("access", 5, 3), ("key_rotation", 12, 9), ("break_even", 9, 10)]
_S1_MERGES = [("traces", 2, "data_where", 2), ("proof", 5, "write_action", 3)]

# Подписи слитых вопросов: по ним человек узнаёт, откуда в ответе-приёмнике взялась строка.
_SOURCE_LABEL = {
    "human_refusal": "Где человек не доверит решение машине",
    "constraints": "Ограничения — сроки и бюджет",
    "confirm_what_runs": "Человек видит ровно исполняемое действие с параметрами",
    "manual_queue": "Ручная очередь",
}
# Прежняя таблица границы: [шаг, что происходит, вердикт, почему] → [шаг As-Is, судьба, кто делает дальше, почему].
_OLD_BOUNDARY = "3:boundary_table"
_MONEY = re.compile(r"(?i)деньг|стоим|\$|cost|бюджет|руб")


def _mark(old_step) -> str:
    return f"(из прежнего шага {int(old_step)})"


def _split(key: str) -> tuple[int, str]:
    s, q = str(key).split(":", 1)
    return int(s), q


def _q(step_id: int, qid: str) -> dict | None:
    try:
        return core.question(step_id, qid)
    except KeyError:
        return None


def _filled(rec: dict | None) -> bool:
    if not rec or rec.get("status") not in ("answered", "needs_detail"):
        return False
    v = rec.get("value")
    return bool(v) if isinstance(v, list) else str(v or "").strip() != ""


def _as_text(value) -> str:
    if isinstance(value, list):
        if value and isinstance(value[0], list):
            return "\n".join(" · ".join(str(c) for c in r if str(c).strip()) for r in value)
        return "; ".join(str(x) for x in value)
    return str(value or "").strip()


def _empty_value(q: dict):
    return [] if q["type"] in ("table", "multi") else ""


def _fit_row(row, n: int) -> list[str]:
    cells = [str(c) for c in (row if isinstance(row, list) else [row])]
    if len(cells) > n:  # лишние колонки не выбрасываются — дописываются в последнюю
        cells = cells[: n - 1] + [" · ".join(c for c in cells[n - 1:] if c)]
    return cells + [""] * (n - len(cells))


def _match_option(q: dict, v):
    """Вариант, переименованный в ядре (например «документы шагов 1–12» → «Ш1–Ш6»), — по тексту до скобки."""
    opts = q.get("options") or []
    if not isinstance(v, str) or not opts or v in opts:
        return v
    head = v.split("(")[0].strip()
    hits = [o for o in opts if o.split("(")[0].strip() == head]
    return hits[0] if len(hits) == 1 else v


def _convert_same(old_key: str, nq: dict, rec: dict) -> dict:
    """Вопрос остался собой: подогнать форму значения под новое ядро (колонки, переименованные варианты)."""
    rec = copy.deepcopy(rec)
    v = rec.get("value")
    if nq["type"] == "table" and isinstance(v, list):
        n = len(nq["columns"])
        rows = []
        for r in v:
            r = list(r) if isinstance(r, list) else [str(r)]
            if old_key == _OLD_BOUNDARY:
                r = (r + [""] * 4)[:4]
                why = "; ".join(x for x in (r[3], f"что происходит: {r[1]}" if r[1] else "") if x)
                r = [r[0], "", r[2], why]
            rows.append(_fit_row(r, n))
        rec["value"] = rows
    elif nq["type"] == "choice":
        rec["value"] = _match_option(nq, v)
    elif nq["type"] == "multi" and isinstance(v, list):
        rec["value"] = [_match_option(nq, x) for x in v]
    return rec


def _same_row(a: str, b: str) -> bool:
    wa = set(re.findall(r"[а-яёa-z0-9]{3,}", str(a).lower()))
    wb = set(re.findall(r"[а-яёa-z0-9]{3,}", str(b).lower()))
    return bool(wa and wb) and (wa <= wb or wb <= wa or len(wa & wb) >= 2)


def _merge(target: dict | None, nq: dict, src: dict, old_key: str, rule: str, req_index: int) -> dict:
    """Слить ответ src (старый вопрос old_key) в ответ-приёмник по правилу ядра. Исходная запись — в merged_from."""
    old_step, old_qid = _split(old_key)
    mark = _mark(old_step)
    created = target is None
    if created:
        target = {"status": src.get("status", "unknown"), "value": _empty_value(nq), "note": "", "updated": src.get("updated"),
                  "senior": None}
    target.setdefault("merged_from", []).append({"from": old_key, "rule": rule, **copy.deepcopy(src)})
    if src.get("note"):
        target["note"] = "\n".join(x for x in (target.get("note") or "", f"{src['note']} {mark}") if x)
    if not _filled(src):
        return target
    text = _as_text(src["value"])
    label = _SOURCE_LABEL.get(old_qid)
    was_open = target.get("status") not in ("answered", "needs_detail") or not _filled(target)
    n = len(nq.get("columns") or [])
    v = target.get("value")
    if rule == "append_text" or (rule != "extra_text" and nq["type"] not in ("table", "choice", "multi")):
        line = f"{label}: {text} {mark}" if label else f"{text} {mark}"
        target["value"] = f"{v}\n{line}" if isinstance(v, str) and v.strip() else line
    elif rule == "extra_text" or nq["type"] in ("choice", "multi"):
        target["extra"] = "\n".join(x for x in (target.get("extra") or "", f"{text} {mark}") if x)
        if was_open:  # выбора ещё нет — вопрос остаётся открытым, но текст не потерян
            target["status"] = "needs_detail"
            target["senior"] = {"verdict": "needs_detail", "follow_up": "Выберите вариант ответа — текст из прежнего "
                                "шага перенесён в дополнительную строку."}
            return target
    else:
        rows = [list(r) for r in (v if isinstance(v, list) else [])]
        if rule == "first_row":
            rows.insert(0, _fit_row([f"{text} {mark}"], n))
        elif rule == "required_row":
            labels = nq.get("required_rows") or []
            lbl = labels[req_index] if req_index < len(labels) else ""
            row = _fit_row([lbl, f"{text} {mark}"] if n > 1 else [f"{lbl}: {text} {mark}"], n)
            rows.insert(min(req_index, len(rows)), row)
        elif rule == "rows_by_step":  # судьба шага As-Is — построчно в таблицу границы
            for fr in src["value"]:
                fr = (list(fr) + ["", "", ""])[:3]
                hit = next((r for r in rows if _same_row(r[0], fr[0])), None)
                if hit is None:
                    rows.append(_fit_row([fr[0], fr[1], "", f"{fr[2]} {mark}".strip()], n))
                    continue
                hit[1] = "; ".join(x for x in (hit[1], fr[1]) if x)
                hit[-1] = "; ".join(x for x in (hit[-1], f"{fr[2]} {mark}" if fr[2] else "") if x)
        elif rule == "column":  # в последнюю колонку таблицы-приёмника
            src_rows = [r if isinstance(r, list) else [str(r)] for r in src["value"]] \
                if isinstance(src["value"], list) else []
            if not isinstance(src["value"], list):  # текст: строка про деньги или первая строка
                hit = next((r for r in rows if _MONEY.search(r[0] or "")), rows[0] if rows else None)
                if hit is None:
                    rows.append(_fit_row(["деньги"] + [""] * (n - 2) + [f"{text} {mark}"], n))
                else:
                    hit[-1] = "; ".join(x for x in (hit[-1], f"{text} {mark}") if x)
            else:  # таблица «лишнее»: строка по названию действия
                for sr in src_rows:
                    sr = [str(c) for c in sr]
                    parts = [f"{k} — {x}" for k, x in zip(("функция", "право", "самостоятельность"), sr[1:4]) if x]
                    cell = f"{'; '.join(parts) or ' · '.join(sr)} {mark}"
                    hit = next((r for r in rows if _same_row(r[0], sr[0])), None)
                    if hit is None:
                        rows.append(_fit_row([sr[0]] + [""] * (n - 2) + [cell], n))
                    else:
                        hit[-1] = "; ".join(x for x in (hit[-1], cell) if x)
        target["value"] = [_fit_row(r, n) for r in rows]
    if was_open:
        target["status"] = "needs_detail" if src.get("status") == "needs_detail" else "answered"
    if not created:
        target.setdefault("migrated", {}).setdefault("changed_by", []).append(old_key)
    return target


def _schema1_to_2(st: dict) -> None:
    answers = st.setdefault("answers", {})
    owners = st.setdefault("risk_owners", {})
    for qid, old, new in _S1_MOVES:
        rec = (answers.get(str(old)) or {}).get(qid)
        if rec and qid not in (answers.get(str(new)) or {}):
            answers.setdefault(str(new), {})[qid] = rec
            del answers[str(old)][qid]
            if f"q:{old}:{qid}" in owners:
                owners[f"q:{new}:{qid}"] = owners.pop(f"q:{old}:{qid}")
    for src, s_old, dst, s_dst in _S1_MERGES:
        rec = (answers.get(str(s_old)) or {}).get(src)
        if not rec:
            continue
        target = answers.setdefault(str(s_dst), {}).get(dst)
        text = str(rec.get("value") or "").strip()
        if rec.get("status") == "answered" and text:
            if target and target.get("status") == "answered":
                target["value"] = f"{target['value']}\n{text}"
            else:
                answers[str(s_dst)][dst] = dict(rec)
        del answers[str(s_old)][src]


def _remap_key(key: str, qmap: dict, smap: dict) -> str:
    """Ключи вида «q:шаг:id», «шаг:id», «шаг.id», «run:N» — на новые места."""
    m = re.fullmatch(r"(q:)?(\d+)([:.])([a-z_]+)", key)
    if m:
        new = qmap.get(f"{m.group(2)}:{m.group(4)}")
        if new:
            ns, nq = _split(new)
            return f"{m.group(1) or ''}{ns}{m.group(3)}{nq}"
        return key
    if key == "run:11":
        return "run:attacks"
    if key == "run:10":
        return "run:eval"
    return key


def _schema2_to_3(st: dict) -> dict:
    mig = core.migration()
    qmap = {str(k): str(v) for k, v in (mig.get("map") or {}).items()}
    rules = {str(k): str(v) for k, v in (mig.get("merge_rules") or {}).items()}
    smap = {int(k): int(v) for k, v in (mig.get("step_map") or {}).items()}
    last = max(smap.values()) if smap else core.step_count()

    def new_step(x) -> int:
        try:
            return smap.get(int(x), min(int(x), last))
        except (TypeError, ValueError):
            return 1

    old_answers = st.get("answers") or {}
    new_answers: dict[str, dict] = {}
    report = {"date": now(), "from_schema": 2, "to_schema": SCHEMA, "moved": 0, "merged": [], "unplaced": {}}
    # 1. вопросы, оставшиеся собой (в том числе переехавшие на другой шаг)
    for okey, nkey in qmap.items():
        if okey in rules:
            continue
        os_, oq = _split(okey)
        rec = (old_answers.get(str(os_)) or {}).get(oq)
        if not rec:
            continue
        ns, nq = _split(nkey)
        q = _q(ns, nq)
        if q is None:
            continue
        new_answers.setdefault(str(ns), {})[nq] = _convert_same(okey, q, rec)
        report["moved"] += 1
    # 2. слитые вопросы — по правилам ядра, в порядке таблицы
    req_seen: dict[str, int] = {}
    for okey, rule in rules.items():
        os_, oq = _split(okey)
        rec = (old_answers.get(str(os_)) or {}).get(oq)
        if not rec or okey not in qmap:
            continue
        ns, nq = _split(qmap[okey])
        q = _q(ns, nq)
        if q is None:
            continue
        idx = 0
        if rule == "required_row":
            idx = req_seen.get(qmap[okey], 0)
            req_seen[qmap[okey]] = idx + 1
        bucket = new_answers.setdefault(str(ns), {})
        bucket[nq] = _merge(bucket.get(nq), q, rec, okey, rule, idx)
        report["merged"].append(f"{okey} → {qmap[okey]} ({rule})")
    # 3. то, чего нет в таблице ядра, не выбрасывается
    for s, recs in old_answers.items():
        for qid, rec in (recs or {}).items():
            if f"{s}:{qid}" not in qmap:
                report["unplaced"][f"{s}:{qid}"] = rec
    st["answers"] = new_answers

    # 4. владельцы рисков, счётчики наставника, риски, рекомендации
    owners = {}
    for k, v in (st.get("risk_owners") or {}).items():
        nk = _remap_key(k, qmap, smap)
        if v and not owners.get(nk):
            owners[nk] = v
    st["risk_owners"] = owners
    mentor_new: dict[str, dict] = {}
    for k, v in (st.get("mentor") or {}).items():
        nk = _remap_key(k, qmap, smap)
        cur = mentor_new.setdefault(nk, {"guided": 0})
        cur["guided"] = int(cur.get("guided", 0)) + int((v or {}).get("guided", 0))
        if (v or {}).get("updated") and str(v["updated"]) > str(cur.get("updated") or ""):
            cur["updated"] = v["updated"]
    if mentor_new:
        st["mentor"] = mentor_new
    for r in st.get("risks_extra") or []:
        r["step"] = new_step(r.get("step", 1))
        if r.get("key"):
            r["key"] = _remap_key(str(r["key"]), qmap, smap)
    scan = (st.get("workspace") or {}).get("last_scan") or {}
    for f in scan.get("findings") or []:
        if "step" in f:
            f["step"] = new_step(f["step"])

    # 5. выводы шагов: 12 → 6, каждый помечен «нужно подтвердить заново»
    old_syn = st.get("synthesis") or {}
    if old_syn:
        syn: dict[str, dict] = {}
        for k in sorted(old_syn, key=lambda x: int(x) if str(x).isdigit() else 99):
            v = old_syn[k] or {}
            ns = str(new_step(k))
            cur = syn.setdefault(ns, {"text": "", "confirmed": v.get("confirmed"), "needs_reconfirm": True,
                                      "from_steps": []})
            part = f"{v.get('text', '')} {_mark(k)}".strip()
            cur["text"] = f"{cur['text']}\n\n{part}" if cur["text"] else part
            cur["from_steps"].append(int(k) if str(k).isdigit() else k)
            if str(v.get("confirmed") or "") > str(cur.get("confirmed") or ""):
                cur["confirmed"] = v["confirmed"]
        st["synthesis"] = syn

    # 6. документы шагов — все устарели; прежние записи сохраняются целиком
    old_art = st.get("artifacts") or {}
    if old_art:
        report["artifacts_old"] = copy.deepcopy(old_art)
        art: dict[str, dict] = {}
        for k, v in old_art.items():
            ns = str(new_step(k))
            if ns not in art or str(v.get("built") or "") > str(art[ns].get("built") or ""):
                art[ns] = {**v, "stale": True}
        st["artifacts"] = art

    # 7. рекомендации: идентификатор — хеш идентификатора риска; пересчитываем на новые места
    from .recommend import _rid
    rid_map: dict[str, str] = {}
    for okey in qmap:
        os_, oq = _split(okey)
        rid_map[_rid(f"q:{os_}:{oq}")] = _rid(_remap_key(f"q:{os_}:{oq}", qmap, smap))
    rid_map[_rid("run:11")] = _rid("run:attacks")
    rid_map[_rid("run:10")] = _rid("run:eval")
    for k in smap:
        rid_map[_rid("doc", k)] = _rid("doc", new_step(k))
    recs = st.get("recs") or {}
    if recs:
        new_recs: dict[str, dict] = {}
        orphans = {}
        for rid, meta in recs.items():
            nrid = rid_map.get(rid, rid)
            if meta.get("defer_step"):
                meta["defer_step"] = new_step(meta["defer_step"])
            if nrid in new_recs:
                orphans[rid] = meta
            else:
                new_recs[nrid] = meta
        st["recs"] = new_recs
        if orphans:
            report["recs_merged"] = orphans

    # 8. где проект стоял на маршруте
    st["current_step"] = new_step(st.get("current_step", 1))
    st["start_step"] = new_step(st.get("start_step", 1))
    st.setdefault("migrations", []).append(report)
    return report


def migrate(st: dict) -> bool:
    """Переносит проект на текущую схему. Возвращает True, если что-то поменялось. Повторный вызов ничего не меняет."""
    try:
        schema = int(st.get("schema", 1))
    except (TypeError, ValueError):
        schema = 1
    if schema >= SCHEMA:
        return False
    if schema < 2:
        _schema1_to_2(st)
    _schema2_to_3(st)
    st["schema"] = SCHEMA
    return True
