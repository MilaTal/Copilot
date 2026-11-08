"""Состояние проекта: один JSON на проект плюс версия на каждое сохранение.

Структура папки проекта:
  data/projects/<id>/state.json      — текущее состояние
  data/projects/<id>/versions/*.json — снимки, последние 200
  data/projects/<id>/workspace.json  — опись разработки Junior Pilot (отдельно, чтобы не раздувать состояние)
  data/projects/<id>/docs/           — документы шагов, паспорт, каркас защиты, протоколы
  data/projects/<id>/prototype/      — схема, сценарий n8n, каркас на Python

Даты внутри — ISO (для сортировки), людям показываются только как дд.мм.гггг (fmt_date, fmt_dt)."""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, core

KEEP_VERSIONS = 200
MAX_TEXT_CHARS = 20_000
MAX_TABLE_ROWS = 300
MAX_CELL_CHARS = 2_000
MAX_NOTE_CHARS = 4_000
_ID_RE = re.compile(r"^[a-z0-9-]{3,64}$")

_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                      "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


# ---------- даты для людей ----------
def fmt_date(iso: str | None) -> str:
    """ISO → дд.мм.гггг."""
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(str(iso)).strftime("%d.%m.%Y")
    except ValueError:
        return str(iso)


def fmt_dt(iso: str | None) -> str:
    """ISO → дд.мм.гггг чч:мм."""
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(str(iso)).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return str(iso)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- замок на данные ----------
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_tls = threading.local()
LOCK_STALE_SEC = 60


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_is_stale(lockfile: Path) -> bool:
    try:
        owner = int(lockfile.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        owner = 0
    if owner and owner != os.getpid() and not _pid_alive(owner):
        return True  # хозяин замка умер — замок снимается сразу, а не через минуту
    try:
        return time.time() - lockfile.stat().st_mtime > LOCK_STALE_SEC
    except FileNotFoundError:
        return False


@contextmanager
def locked(pid: str, wait_sec: float = 30):
    """Замок на данные проекта: «прочитать — изменить — сохранить» идёт целиком под ним.

    Внутри процесса — RLock, между процессами (сервер и командная строка) — файл-замок с номером процесса.
    Замок умершего процесса снимается сразу. Долгие вызовы (модель, агент) под замком не делают:
    снимок → вызов → слияние со свежим состоянием."""
    d = project_dir(pid)
    if not (d / "state.json").exists():
        raise FileNotFoundError(f"проект {pid} не найден")
    with _LOCKS_GUARD:
        lk = _LOCKS.setdefault(pid, threading.RLock())
    with lk:
        lockfile = d / "state.lock"
        depth = getattr(_tls, pid, 0)
        if depth == 0:
            t0 = time.monotonic()
            while True:
                try:
                    fd = os.open(lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd, str(os.getpid()).encode())
                    os.close(fd)
                    break
                except FileExistsError:
                    if _lock_is_stale(lockfile):
                        try:
                            lockfile.unlink(missing_ok=True)
                        except PermissionError:
                            pass
                        continue
                    if time.monotonic() - t0 > wait_sec:
                        raise TimeoutError("проект занят другим действием — повторите через несколько секунд")
                    time.sleep(0.05)
        setattr(_tls, pid, depth + 1)
        try:
            yield
        finally:
            setattr(_tls, pid, depth)
            if depth == 0:
                for _ in range(20):
                    try:
                        lockfile.unlink(missing_ok=True)
                        break
                    except PermissionError:
                        time.sleep(0.05)


# ---------- файлы с повтором (Windows не даёт заменить файл, пока его кто-то читает) ----------
def _read_text(path: Path, attempts: int = 40) -> str:
    for i in range(attempts):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.025)
    return ""


def _replace(tmp: Path, dst: Path, attempts: int = 80) -> None:
    for i in range(attempts):
        try:
            tmp.replace(dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.025)


# ---------- проекты ----------
def slugify(name: str) -> str:
    s = "".join(_TRANSLIT.get(c, c) for c in name.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40].strip("-") or "proekt"
    return f"{s}-{secrets.token_hex(2)}"


def project_dir(pid: str) -> Path:
    if not isinstance(pid, str) or not _ID_RE.match(pid):
        raise ValueError("неверный идентификатор проекта")
    return config.projects_dir() / pid


def archive_project(pid: str) -> str:
    """Проект уходит в архив, а не удаляется: папка переносится в data/_archive_projects/<дд.мм.гггг>/<pid>.
    Так кнопка «В архив» ничего не теряет, а из списка проект исчезает."""
    import shutil
    src = project_dir(pid)
    if not src.is_dir():
        raise FileNotFoundError(pid)
    dst = config.data_dir() / "_archive_projects" / datetime.now().strftime("%d.%m.%Y") / pid
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return str(dst)


def archive_test_projects(ids: list[str]) -> Path | None:
    """Проекты, созданные проверочным прогоном, — из общего списка в data/_archive_test_projects/<дд.мм.гггг>/.
    Переносятся только переданные id: проект, заведённый человеком во время прогона, не трогается."""
    import shutil
    dst = config.data_dir() / "_archive_test_projects" / datetime.now().strftime("%d.%m.%Y")
    moved = 0
    for pid in ids:
        src = config.projects_dir() / pid
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst / pid))
            moved += 1
    return dst if moved else None


@contextmanager
def archived_test_run():
    """Обёртка для проверочных скриптов: проекты, созданные внутри, после прогона (и при сбое) уходят в архив."""
    created: list[str] = []
    orig = globals()["create"]

    def recording(*a, **k):
        st = orig(*a, **k)
        created.append(st["id"])
        return st
    globals()["create"] = recording
    try:
        yield created
    finally:
        globals()["create"] = orig
        dst = archive_test_projects(created)
        if dst:
            print(f"Проекты прогона ({len(created)}) перенесены в архив: {dst}")


def create(name: str, start_step: int = 1, description: str = "", domain: str = "") -> dict:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("нужно название проекта")
    name = name.strip()[:200]
    n = core.step_count()
    try:
        start_step = int(start_step)
    except (TypeError, ValueError):
        raise ValueError(f"шаг старта — число от 1 до {n}")
    if not core.has_step(start_step):
        raise ValueError(f"шаг старта — число от 1 до {n}")
    domain = str(domain or "").strip()
    if domain and not core.domain(domain):
        raise ValueError("нет такой отрасли")
    pid = slugify(name)
    st = {
        "id": pid, "name": name, "description": str(description or "").strip()[:MAX_TEXT_CHARS], "domain": domain,
        "created": now(), "updated": now(), "start_step": start_step, "current_step": start_step,
        "answers": {}, "artifacts": {}, "risks_extra": [], "risk_owners": {},
        "target": {}, "attack_runs": [], "eval_runs": [], "history": [], "schema": _schema(),
    }
    d = project_dir(pid)
    (d / "versions").mkdir(parents=True, exist_ok=True)
    (d / "docs").mkdir(exist_ok=True)
    save(st, "проект создан")
    return st


def list_projects() -> list[dict]:
    """Список проектов. Испорченный проект не роняет список — он показывается с пометкой."""
    out = []
    for d in sorted(config.projects_dir().iterdir()):
        f = d / "state.json"
        if not f.exists():
            continue
        try:
            st = json.loads(_read_text(f))
            if not _valid_state(st, d.name):
                raise ValueError("форма файла")
            cur = int(st.get("current_step", 1))
            if int(st.get("schema", 1) or 1) < _schema():  # проект ещё не переехал — номер шага по таблице ядра
                cur = int((core.migration().get("step_map") or {}).get(cur, 1))
            out.append({"id": st["id"], "name": str(st["name"]), "updated": st.get("updated"),
                        "updated_text": fmt_dt(st.get("updated")), "current_step": cur})
        except Exception:  # noqa: BLE001 — любой мусор в одном проекте не должен ронять список
            out.append({"id": d.name, "name": f"{d.name} — файл состояния повреждён, восстановите версию",
                        "updated": "", "updated_text": "", "current_step": 1, "broken": True})
    out.sort(key=lambda p: p.get("updated") or "", reverse=True)
    return out


def load(pid: str) -> dict:
    f = project_dir(pid) / "state.json"
    if not f.exists():
        raise FileNotFoundError(f"проект {pid} не найден")
    try:
        st = json.loads(_read_text(f))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise FileNotFoundError(f"файл состояния проекта {pid} повреждён — верните последнюю целую версию")
    if not _valid_state(st, pid):
        raise FileNotFoundError(f"файл состояния проекта {pid} повреждён — верните последнюю целую версию")
    _backup_before_migration(pid, st, f)
    return _migrate(st)


def _schema() -> int:
    from .migration import SCHEMA  # ленивый импорт: migration читает state
    return SCHEMA


def _backup_before_migration(pid: str, st: dict, f: Path) -> Path | None:
    """Перед переездом на новую схему — копия файла проекта рядом, один раз: backup-schema<N>-<дд.мм.гггг>.json."""
    try:
        old = int(st.get("schema", 1) or 1)
    except (TypeError, ValueError):
        old = 1
    if old >= _schema():
        return None
    d = project_dir(pid)
    if any(d.glob(f"backup-schema{old}-*.json")):
        return None
    dst = d / f"backup-schema{old}-{datetime.now().strftime('%d.%m.%Y')}.json"
    try:
        dst.write_text(_read_text(f), encoding="utf-8")
    except OSError:
        return None
    return dst


def _valid_state(st, pid: str) -> bool:
    return (isinstance(st, dict) and st.get("id") == pid and isinstance(st.get("name"), str)
            and isinstance(st.get("answers", {}), dict))


def save(st: dict, reason: str) -> None:
    st["updated"] = now()
    st.setdefault("history", []).append({"ts": st["updated"], "what": reason})
    st["history"] = st["history"][-300:]
    d = project_dir(st["id"])
    d.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(st, ensure_ascii=False, indent=1)
    tmp = d / f"state.json.{os.getpid()}.{threading.get_ident()}.tmp"
    tmp.write_text(payload, encoding="utf-8")
    _replace(tmp, d / "state.json")
    vdir = d / "versions"
    vdir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    (vdir / f"{stamp}.json").write_text(payload, encoding="utf-8")
    versions_list = sorted(vdir.glob("*.json"))
    for old in versions_list[:-KEEP_VERSIONS]:
        try:
            old.unlink()
        except PermissionError:
            pass


def _version_label(stem: str) -> str:
    try:
        return datetime.strptime(stem[:15], "%Y%m%d-%H%M%S").strftime("%d.%m.%Y %H:%M:%S")
    except ValueError:
        return stem


def versions(pid: str) -> list[dict]:
    vdir = project_dir(pid) / "versions"
    out = []
    if not vdir.exists():
        return out
    for f in sorted(vdir.glob("*.json"), reverse=True)[:100]:
        try:
            st = json.loads(_read_text(f))
            what = (st.get("history") or [{}])[-1].get("what", "")
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            what = "файл повреждён"
        out.append({"version": f.stem, "label": _version_label(f.stem), "what": what})
    return out


def restore(pid: str, version: str) -> dict:
    if not isinstance(version, str) or not re.match(r"^[0-9-]{8,40}$", version):
        raise ValueError("неверная версия")
    f = project_dir(pid) / "versions" / f"{version}.json"
    if not f.exists():
        raise ValueError("такой версии нет")
    try:
        st = json.loads(_read_text(f))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("версия повреждена")
    if not _valid_state(st, pid):
        raise ValueError("версия повреждена")
    save(st, f"восстановлена версия от {_version_label(version)}")
    return _migrate(st)


# ---------- ответы ----------
def _migrate(st: dict) -> dict:
    from . import migration  # ленивый импорт: migration читает state
    migration.migrate(st)
    return st


def required_rows_template(q: dict) -> list[list[str]]:
    """Черновик пустой таблицы: обязательные строки ядра (required_rows) подписью в первой ячейке."""
    n = len(q.get("columns") or [])
    return [[lbl] + [""] * (n - 1) for lbl in q.get("required_rows") or []] if n else []


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[а-яёa-z]{4,}", str(text).lower())}


def missing_required_rows(q: dict, value) -> list[str]:
    """Каких обязательных строк нет. Строка есть, если в какой-то заполненной строке таблицы видны слова подписи
    (не меньше трети основ). Если обязательная строка одна, она ещё и узнаётся по месту: первая заполненная строка
    («первая строка — главное число ЛПР»). Строка-черновик — одна подпись без содержания — не считается."""
    labels = q.get("required_rows") or []
    rows = [[str(c).strip() for c in (r if isinstance(r, list) else [r])] for r in (value if isinstance(value, list) else [])]
    filled = [r for r in rows if any(r[1:]) or (r and r[0] and r[0] not in labels)]
    out = []
    for lbl in labels:
        st_ = _stems(lbl)
        need = max(1, -(-len(st_) // 3))
        hit = any(len(st_ & _stems(" ".join(r))) >= need for r in filled)
        if not hit and len(labels) == 1 and rows and rows[0] in filled:
            hit = True
        if not hit:
            out.append(lbl)
    return out


def get_answer(st: dict, step_id: int, qid: str) -> dict | None:
    return (st.get("answers") or {}).get(str(step_id), {}).get(qid)


def normalize_value(q: dict, value: Any) -> Any:
    t = q["type"]
    if t == "table":
        if isinstance(value, str):
            value = [r for r in value.splitlines() if r.strip()]
        if not isinstance(value, list):
            raise ValueError("таблица — список строк")
        if len(value) > MAX_TABLE_ROWS:
            raise ValueError(f"в таблице больше {MAX_TABLE_ROWS} строк — сократите до сути")
        cols = len(q["columns"])
        rows = []
        for row in value:
            if isinstance(row, str):
                row = [c.strip() for c in row.split("·")] if "·" in row else [row]
            if not isinstance(row, (list, tuple)):
                raise ValueError("строка таблицы — список ячеек")
            cells = [str(c).strip() for c in list(row)[:cols]]
            if any(len(c) > MAX_CELL_CHARS for c in cells):
                raise ValueError(f"ячейка длиннее {MAX_CELL_CHARS} знаков — сократите")
            cells += [""] * (cols - len(cells))
            if any(cells):
                rows.append(cells)
        return rows
    if t == "multi":
        if isinstance(value, str):
            value = [v.strip() for v in value.split(";") if v.strip()]
        if not isinstance(value, list):
            raise ValueError("ответ — список вариантов")
        out = [str(v) for v in value]
        bad = [v for v in out if q.get("options") and v not in q["options"]]
        if bad:
            raise ValueError("нет такого варианта: " + "; ".join(bad) + ". Допустимые: " + "; ".join(q["options"]))
        return out
    if t == "number":
        if value in (None, ""):
            return ""
        try:
            return float(str(value).replace(",", ".").replace(" ", ""))
        except ValueError:
            raise ValueError("ожидается число")
    text = str(value if value is not None else "").strip()
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"ответ длиннее {MAX_TEXT_CHARS} знаков — оставьте суть, детали приложите документом")
    return text


def is_empty(q: dict, value: Any) -> bool:
    if q["type"] in ("table", "multi"):
        return not value
    return value in (None, "")


def set_answer(st: dict, step_id: int, qid: str, status: str, value: Any = None, note: str = "",
               extra: str | None = None) -> dict:
    """extra — свободный текст рядом с вариантами (поле ядра extra_text); у вопроса без extra_text не хранится."""
    q = core.question(step_id, qid)
    if status not in ("answered", "unknown", "skipped"):
        raise ValueError("статус ответа — answered, unknown или skipped")
    value = normalize_value(q, value) if value is not None else ("" if q["type"] not in ("table", "multi") else [])
    if status == "answered" and is_empty(q, value):
        raise ValueError("пустой ответ — выберите «Не знаю» или «Сознательно опускаю сейчас»")
    if status == "answered" and q["type"] == "choice" and q.get("options") and value not in q["options"]:
        raise ValueError("нет такого варианта. Допустимые: " + "; ".join(q["options"]))
    note = str(note or "").strip()[:MAX_NOTE_CHARS]
    rec = {"status": status, "value": value, "note": note, "updated": now(), "senior": None}
    prev = (st.get("answers") or {}).get(str(step_id), {}).get(qid) or {}
    if q.get("extra_text"):
        text = prev.get("extra", "") if extra is None else str(extra or "").strip()[:MAX_NOTE_CHARS]
        if text:
            rec["extra"] = text
    if prev.get("merged_from"):  # исходные ответы прежних шагов не теряются при правке
        rec["merged_from"] = prev["merged_from"]
    st.setdefault("answers", {}).setdefault(str(step_id), {})[qid] = rec
    st["current_step"] = int(step_id)
    art = st.get("artifacts", {}).get(str(step_id))
    if art:
        art["stale"] = True
    return rec


def set_senior_review(st: dict, step_id: int, qid: str, review: dict) -> None:
    rec = get_answer(st, step_id, qid)
    if not rec:
        return
    rec["senior"] = review
    rec.pop("senior_error", None)  # проверка прошла — прежний сбой больше не показывается
    if review.get("verdict") == "needs_detail" and rec["status"] == "answered":
        rec["status"] = "needs_detail"


def set_senior_error(st: dict, step_id: int, qid: str, message: str) -> None:
    """Сбой проверки хранится в ответе: после перезагрузки видно, что Senior Pilot ответ не проверял."""
    rec = get_answer(st, step_id, qid)
    if not rec:
        return
    rec["senior_error"] = str(message or "").strip()[:600]


def close_risks(st: dict, key: str, source: str) -> int:
    n = 0
    for r in st.get("risks_extra") or []:
        if r.get("status") == "open" and r.get("key") == key and r.get("source") == source:
            r["status"] = "closed"
            n += 1
    return n


def add_risk(st: dict, step_id: int, text: str, level: str = "caution", source: str = "manual", owner: str = "",
             key: str | None = None) -> dict:
    """Риск без дублей: такой же открытый риск того же шага и источника не заводится второй раз."""
    text = str(text).strip()[:600]
    level = level if level in ("warning", "caution") else "caution"
    for r in st.setdefault("risks_extra", []):
        if r.get("status") == "open" and r["step"] == int(step_id) and r["text"] == text and r["source"] == source:
            if level == "warning":
                r["level"] = "warning"
            return r
    risk = {"id": f"x{secrets.token_hex(3)}", "step": int(step_id), "text": text, "level": level,
            "source": source, "owner": str(owner or "").strip()[:120], "status": "open", "created": now()}
    if key:
        risk["key"] = key
    st["risks_extra"].append(risk)
    return risk
