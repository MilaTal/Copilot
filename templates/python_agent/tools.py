"""Инструменты агента. Этот файл дописывает инженер проекта: copilot его не перетирает при пересборке каркаса.

Правила:
  — права ключа инструмента — минимально нужные (что умеет ключ, то умеет агент);
  — у записи «с ключом идемпотентности» ключ строится из бизнес-полей, а не из времени;
  — действие класса «повтор недопустим» сюда не попадает: агент отправляет его человеку на подтверждение."""
from __future__ import annotations

import hashlib
import json


class ToolError(Exception):
    pass


def idempotency_key(name: str, args: dict) -> str:
    """Ключ из имени инструмента и аргументов. Замените на бизнес-поля, например «номер пункта + строка программы»."""
    raw = name + "|" + json.dumps(args, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def call(name: str, args: dict) -> str:
    fn = REGISTRY.get(name)
    if not fn:
        raise ToolError(f"инструмент {name} не реализован")
    return fn(**args)


# ---- реализации: по функции на инструмент из config.json ----

def example_read(query: str = "") -> str:
    """Пример инструмента чтения. Замените на вызов своей системы."""
    return f"найдено 0 записей по запросу «{query}»"


REGISTRY = {
    "example_read": example_read,
}
