"""Пути и загрузка окружения. Пути читаются при каждом вызове, чтобы тесты могли подменить их через окружение."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = ROOT / "core"
STATIC_DIR = ROOT / "static"
TEMPLATES_DIR = ROOT / "templates"


def data_dir() -> Path:
    d = Path(os.environ.get("COPILOT_DATA_DIR", Path.home() / ".ai-automation-copilot"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def projects_dir() -> Path:
    d = data_dir() / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def models_file() -> Path:
    env = os.environ.get("COPILOT_MODELS_FILE")
    if env:
        return Path(env)
    own = data_dir() / "models.yaml"  # своя копия роутера в папке данных — правки экрана переживают обновление
    if not own.exists():
        own.write_text((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    return own


def cost_log() -> Path:
    return data_dir() / "cost_log.jsonl"


def load_env_local() -> None:
    """Читает .env.local в окружение процесса. Значения никуда не выводятся.

    Запуск голым uvicorn теряет .env.local, поэтому приложение читает файл само при старте."""
    path = data_dir() / ".env.local"
    if not path.exists():
        path = ROOT / ".env.local"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
