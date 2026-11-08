"""Доступ к разработке Junior Pilot: рабочая папка или git-репозиторий, только на чтение.

Что делает модуль:
  — опись файлов со снимком хешей и разница с прошлой проверкой;
  — у git — текущий коммит и незакоммиченные изменения (команды только на чтение, .git не трогается);
  — проверки кодом по критериям шагов (потолки, идемпотентность, повторы, триггеры n8n, секреты в коде);
  — подготовка материалов для модели с маскированием секретов и выбором локальной или облачной точки.

Что модуль не делает никогда: не пишет в папку Junior Pilot, не читает .env*, ключи, сертификаты и бинарные файлы."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from . import config

IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", ".idea", ".vscode",
               ".pytest_cache", ".mypy_cache", ".next", "site-packages", ".tox"}
SECRET_FILE_RE = re.compile(r"(^\.env)|(\.pem$)|(\.key$)|(\.pfx$)|(\.p12$)|(^id_rsa)|(^id_ed25519)|(secret)|(credential)",
                            re.I)
TEXT_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".md", ".txt", ".toml", ".cfg", ".ini",
            ".sql", ".sh", ".ps1", ".html", ".css", ".csv", ".env.example", ".ipynb"}
MAX_FILE_BYTES = 300_000
MAX_FILES = 3000

SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)"), "закрытый ключ"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]{16,}=*"), "токен после Bearer"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "JWT"),
    (re.compile(r"xox[abposr]-[A-Za-z0-9-]{10,}"), "токен Slack"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), "ключ Google"),
    (re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s:/@'\"]+:[^\s@'\"]{3,}@"), "пароль в строке подключения"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "ключ вида sk-…"),
    (re.compile(r"sk-or-v1-[A-Za-z0-9]{20,}"), "ключ OpenRouter"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "ключ Anthropic"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "ключ AWS"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), "токен GitHub"),
    (re.compile(r"(?i)[\"']?(api[_-]?key|access[_-]?token|token|secret|client[_-]?secret|password|passwd|pwd)[\"']?\s*[:=]\s*[\"']?[^\s\"',}{]{8,}[\"']?"),
     "секрет в присваивании"),
]
# Для находки «секрет в коде» — только явные литералы: вызов get_token() секретом не считается.
SECRET_DETECT = [(rx, label) for rx, label in SECRET_PATTERNS if label != "секрет в присваивании"] + [
    (re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd)\s*[:=]\s*['\"][^'\"\s]{12,}['\"]"), "секрет в присваивании"),
]
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml", ".ps1", ".sh"}


def _is_secret_file(name: str) -> bool:
    if name.endswith(".example") or name.endswith(".sample"):
        return False
    return bool(SECRET_FILE_RE.search(name))


def mask_secrets(text: str) -> tuple[str, int]:
    count = 0
    for rx, _ in SECRET_PATTERNS:
        text, n = rx.subn("«СКРЫТО»", text)
        count += n
    return text, count


def validate_path(path: str) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("укажите путь к папке")
    if path.strip().startswith(("\\\\", "//")):
        raise ValueError("сетевые пути не подключаются — укажите локальную папку")
    p = Path(path).expanduser()
    if not p.exists() or not p.is_dir():
        raise ValueError("папка не найдена")
    p = p.resolve()
    own = config.ROOT.resolve()
    if p == own or own in p.parents:
        raise ValueError("это папка самого copilot — укажите папку проекта Junior Pilot")
    return p


def _is_link(p: Path) -> bool:
    try:
        return p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
    except OSError:
        return True


def _walk(root: Path):
    """Обход без перехода по ссылкам и junction: чужие папки (в том числе сам copilot) не протаскиваются."""
    own = config.ROOT.resolve()
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if _is_link(e):
                continue
            if e.is_dir():
                if e.name in IGNORE_DIRS:
                    continue
                try:
                    r = e.resolve()
                except OSError:
                    continue
                if r == own or own in r.parents:
                    continue
                stack.append(e)
            elif e.is_file():
                yield e


def inventory(root: Path) -> list[dict]:
    files = []
    for f in _walk(root):
        if len(files) >= MAX_FILES:
            break
        rel = f.relative_to(root)
        name = f.name
        item = {"path": rel.as_posix(), "size": f.stat().st_size}
        if _is_secret_file(name):
            item["skipped"] = "секрет — не читается"
        elif f.suffix.lower() not in TEXT_EXT and not name.lower().endswith(".env.example"):
            item["skipped"] = "не текст"
        elif item["size"] > MAX_FILE_BYTES:
            item["skipped"] = "больше 300 КБ"
        else:
            item["sha1"] = hashlib.sha1(f.read_bytes()).hexdigest()
        files.append(item)
    return files


def git_info(root: Path) -> dict:
    if not (root / ".git").exists():
        return {"is_git": False}

    def run(*args):
        try:
            r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=15,
                               encoding="utf-8", errors="replace")
            return r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""
    return {"is_git": True, "commit": run("rev-parse", "--short", "HEAD"), "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": [l for l in run("status", "--porcelain").splitlines()][:200],
            "last_message": run("log", "-1", "--pretty=%s")}


def diff_inventory(prev: list[dict] | None, cur: list[dict]) -> dict:
    prev_map = {f["path"]: f.get("sha1") for f in prev or []}
    cur_map = {f["path"]: f.get("sha1") for f in cur}
    return {
        "added": sorted(p for p in cur_map if p not in prev_map),
        "removed": sorted(p for p in prev_map if p not in cur_map),
        "changed": sorted(p for p in cur_map if p in prev_map and cur_map[p] != prev_map[p]),
    }


# ---------- проверки кодом ----------
def _st(qid: str) -> int:
    """Шаг находки — тот, где в ядре живёт вопрос о проверяемом механизме."""
    from . import core
    return core.qstep(qid)


CODE_CHECKS = [
    {"id": "limits", "qid": "limits_table", "level": "warning",
     "rx": re.compile(r"(?i)(max_?steps|step_?limit|max_?iterations|maxIterations|max_?cost|cost_?limit|budget_usd|wall_?time|max_?calls)"),
     "found": "потолки прогона найдены в коде", "missing": "в коде не найдено потолков шагов, денег или времени"},
    {"id": "limit_outcome", "qid": "limit_outcome", "level": "warning",
     "rx": re.compile(r"(?i)(stopped_by_limit|остановлено по пределу|LimitReached|limit_reached)"),
     "found": "исход «остановлено по пределу» различим", "missing": "не найден отдельный исход «остановлено по пределу»"},
    {"id": "idempotency", "qid": "idempotency", "level": "warning",
     "rx": re.compile(r"(?i)(idempoten|dedup|fingerprint|already_done|unique_key|ключ идемпотентности)"),
     "found": "есть признаки идемпотентности", "missing": "не найден ключ идемпотентности — повтор может создать дубли"},
    {"id": "retry", "qid": "failures_table", "level": "caution",
     "rx": re.compile(r"(?i)(tenacity|@retry|backoff|max_retries|retryOnFail|\bretries\b|retryable|for attempt in range)"),
     "found": "повторы при сбое настроены", "missing": "не найдено повторов при сетевом сбое"},
    {"id": "queue", "qid": "escalation", "level": "caution",
     "rx": re.compile(r"(?i)(manual_queue|human_queue|очередь|dead_letter|escalat)"),
     "found": "есть ручная очередь или эскалация", "missing": "не найдено ручной очереди для отказов"},
    {"id": "cost_log", "qid": "runs_table", "level": "caution",
     "rx": re.compile(r"(?i)(cost_usd|prompt_tokens|input_tokens|usage\.|trace)"),
     "found": "стоимость и токены пишутся в журнал", "missing": "не найдено журнала стоимости и токенов по запуску"},
]


_TRIPLE = re.compile(r'("""|\'\'\')[\s\S]*?\1')


def _strip_comments(text: str, ext: str) -> str:
    """Убирает комментарии и докстроки, сохраняя номера строк: слово в описании — не механизм."""
    if ext == ".py":
        text = _TRIPLE.sub(lambda m: "\n" * m.group().count("\n"), text)
    out = []
    for line in text.splitlines():
        s = line.lstrip()
        if (ext in (".py", ".yaml", ".yml", ".toml", ".sh", ".ps1") and s.startswith("#")) or \
                (ext in (".js", ".ts", ".tsx", ".jsx") and s.startswith("//")):
            out.append("")
        else:
            out.append(line)
    return "\n".join(out)


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def run_checks(root: Path, files: list[dict]) -> list[dict]:
    findings = []
    hits = {c["id"]: [] for c in CODE_CHECKS}
    has_tests = False
    n8n_count = 0
    for item in files:
        if "sha1" not in item:
            continue
        path = root / item["path"]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        name = Path(item["path"]).name
        is_code = Path(item["path"]).suffix.lower() in CODE_EXT
        if name.startswith("test_") or "/tests/" in "/" + item["path"]:
            has_tests = True
        for rx, label in SECRET_DETECT:
            m = rx.search(text)
            if m:
                findings.append({"check": "secret_in_code", "step": _st("key_rotation"), "level": "warning", "file": item["path"],
                                 "line": _line_of(text, m.start()),
                                 "text": f"в коде лежит {label} — вынести в переменные окружения и перевыпустить ключ"})
                break
        if re.search(r"host\s*=\s*['\"]0\.0\.0\.0['\"]|--host\s+0\.0\.0\.0", text):
            findings.append({"check": "open_host", "step": _st("hosting_table"), "level": "caution", "file": item["path"],
                             "line": _line_of(text, text.find("0.0.0.0")),
                             "text": "сервис слушает все сетевые интерфейсы — порт наружу должен быть решением, а не умолчанием"})
        if is_code:
            code_text = _strip_comments(text, Path(item["path"]).suffix.lower())
            for c in CODE_CHECKS:
                m = c["rx"].search(code_text)
                if m:
                    hits[c["id"]].append(f"{item['path']}:{_line_of(code_text, m.start())}")
        if item["path"].endswith(".json") and '"nodes"' in text and '"connections"' in text:
            n8n_count += 1
            findings.extend(check_n8n(item["path"], text))
    code_files = [f for f in files if "sha1" in f and Path(f["path"]).suffix in (".py", ".js", ".ts")]
    if code_files or n8n_count:
        for c in CODE_CHECKS:
            if hits[c["id"]]:
                findings.append({"check": c["id"], "step": _st(c["qid"]), "level": "ok", "file": hits[c["id"]][0],
                                 "line": None, "text": c["found"], "where": hits[c["id"]][:5]})
            else:
                findings.append({"check": c["id"], "step": _st(c["qid"]), "level": c["level"], "file": None, "line": None,
                                 "text": c["missing"]})
        findings.append({"check": "tests", "step": _st("eval_table"), "level": "ok" if has_tests else "caution", "file": None,
                         "line": None, "text": "тесты есть" if has_tests else "тестов не найдено"})
    else:
        findings.append({"check": "no_code", "step": _st("prototype_run"), "level": "caution", "file": None, "line": None,
                         "text": "в папке нет кода и сценариев n8n — проверять сборку пока нечего"})
    return findings


def check_n8n(path: str, text: str) -> list[dict]:
    try:
        wf = json.loads(text, strict=False)
    except json.JSONDecodeError:
        return [{"check": "n8n_json", "step": _st("prototype_run"), "level": "caution", "file": path, "line": None,
                 "text": "сценарий n8n не разбирается как JSON"}]
    nodes = wf.get("nodes") or []
    out = []
    types = [str(n.get("type", "")) for n in nodes]
    if not any("trigger" in t.lower() or "webhook" in t.lower() for t in types):
        out.append({"check": "n8n_trigger", "step": _st("trigger"), "level": "warning", "file": path, "line": None,
                    "text": "в сценарии нет триггера — запуск только кнопкой"})
    for n in nodes:
        t = str(n.get("type", ""))
        name = n.get("name", t)
        if t.endswith(".agent"):
            opts = (n.get("parameters") or {}).get("options") or {}
            if "maxIterations" not in opts:
                out.append({"check": "n8n_max_iter", "step": _st("limits_table"), "level": "warning", "file": path, "line": None,
                            "text": f"узел «{name}»: не задан лимит итераций агента (maxIterations)"})
            if n.get("onError") in ("continueRegularOutput",):
                out.append({"check": "n8n_continue", "step": _st("limit_outcome"), "level": "warning", "file": path, "line": None,
                            "text": f"узел «{name}»: при ошибке продолжает обычной веткой — превышение предела может уйти в «успех»"})
        if n.get("retryOnFail") and n.get("onError") not in (None, "stopWorkflow"):
            out.append({"check": "n8n_retry", "step": _st("failures_table"), "level": "caution", "file": path, "line": None,
                        "text": f"узел «{name}»: повтор при сбое работает только при остановке сценария по ошибке"})
    if not (wf.get("settings") or {}).get("errorWorkflow"):
        out.append({"check": "n8n_error_wf", "step": _st("failures_table"), "level": "caution", "file": path, "line": None,
                    "text": "у сценария не назначен сценарий обработки ошибок (Error Workflow)"})
    return out


def _inventory_file(st: dict):
    from .state import project_dir
    try:
        d = project_dir(st.get("id", ""))
    except ValueError:
        return None
    return d / "workspace.json" if d.exists() else None


def scan(st: dict) -> dict:
    ws = st.get("workspace") or {}
    if not ws.get("path"):
        raise ValueError("рабочая папка не подключена")
    root = validate_path(ws["path"])
    files = inventory(root)
    inv_file = _inventory_file(st)
    prev = None
    if inv_file and inv_file.exists():
        try:
            prev = json.loads(inv_file.read_text(encoding="utf-8")).get("files")
        except (json.JSONDecodeError, OSError):
            prev = None
    report = {
        "path": str(root), "files_total": len(files), "files_read": sum(1 for f in files if "sha1" in f),
        "skipped": [f for f in files if "skipped" in f][:200],
        "git": git_info(root), "diff": diff_inventory(prev, files) if prev else None,
        "findings": run_checks(root, files),
    }
    ws.pop("last_inventory", None)
    if inv_file:
        inv_file.write_text(json.dumps({"path": str(root), "files": files}, ensure_ascii=False), encoding="utf-8")
    ws["last_scan"] = {k: report[k] for k in ("files_total", "files_read", "git", "diff", "findings")}
    st["workspace"] = ws
    return report


def material_for_model(st: dict, max_chars: int = 60_000, focus: list[str] | None = None) -> dict:
    """Собирает текст материалов для модели: сначала файлы с находками и изменённые, секреты замаскированы."""
    ws = st.get("workspace") or {}
    root = validate_path(ws["path"])
    inv_file = _inventory_file(st)
    files = None
    if inv_file and inv_file.exists():
        try:
            files = json.loads(inv_file.read_text(encoding="utf-8")).get("files")
        except (json.JSONDecodeError, OSError):
            files = None
    files = [f for f in (files or inventory(root)) if "sha1" in f]
    scan_info = ws.get("last_scan") or {}
    priority = set(focus or [])
    for fnd in scan_info.get("findings", []):
        for w in fnd.get("where", []) or ([fnd["file"]] if fnd.get("file") else []):
            priority.add(w.split(":")[0])
    diff = scan_info.get("diff") or {}
    priority.update(diff.get("added", []) + diff.get("changed", []))
    ordered = sorted(files, key=lambda f: (f["path"] not in priority, Path(f["path"]).suffix not in (".py", ".json", ".md"), f["size"]))
    parts, used, masked, included = [], 0, 0, []
    for f in ordered:
        try:
            text = (root / f["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text, n = mask_secrets(text)
        masked += n
        chunk = f"\n=== {f['path']} ===\n{text}"
        if used + len(chunk) > max_chars:
            if used + 2000 < max_chars:
                chunk = chunk[: max_chars - used] + "\n…[обрезано]"
            else:
                continue
        parts.append(chunk)
        used += len(chunk)
        included.append(f["path"])
        if used >= max_chars:
            break
    return {"text": "".join(parts), "files": included, "chars": used, "masked": masked,
            "cloud_allowed": bool(ws.get("cloud_allowed"))}
