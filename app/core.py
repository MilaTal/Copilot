"""Загрузка ядра: шаги, паттерны, атаки, подсказки. Один источник и для приложения, и для работы в чате."""
from __future__ import annotations

from functools import lru_cache

import yaml

from .config import CORE_DIR

CLOSED_STATUSES = {"answered", "unknown", "skipped"}
STATUS_LABELS = {
    "answered": "Ответ",
    "unknown": "Не знаю",
    "skipped": "Сознательно опущено сейчас",
    "needs_detail": "Senior Pilot подсказывает, что добавить",
}


@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    with open(CORE_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def steps() -> list[dict]:
    out = _load("steps.yaml")["steps"]
    for s in out:
        for q in s["questions"]:
            q.setdefault("required", True)
            q.setdefault("risk", {"level": "caution", "text": "Вопрос открыт."})
    return out


def levels() -> dict:
    return _load("steps.yaml").get("levels") or {}


def level(step_id: int) -> dict:
    lv = levels()
    key = step(step_id).get("level", "")
    return {"id": key, **lv.get(key, {"title": "", "expect": ""})}


def phases() -> list[dict]:
    return _load("steps.yaml")["phases"]


def step(step_id: int) -> dict:
    for s in steps():
        if s["id"] == int(step_id):
            return s
    raise KeyError(f"нет шага {step_id}")


def question(step_id: int, qid: str) -> dict:
    for q in step(step_id)["questions"]:
        if q["id"] == qid:
            return q
    raise KeyError(f"нет вопроса {qid} в шаге {step_id}")


# ---------- маршрут: число шагов, коды и slug берутся только отсюда ----------
def step_ids() -> list[int]:
    return [s["id"] for s in steps()]


def step_count() -> int:
    return len(steps())


def has_step(step_id) -> bool:
    try:
        return int(step_id) in step_ids()
    except (TypeError, ValueError):
        return False


def step_by_slug(slug: str) -> dict:
    for s in steps():
        if s.get("slug") == slug:
            return s
    raise KeyError(f"нет шага {slug}")


def sid(slug: str) -> int:
    """Номер шага по slug: код не держит номера шагов в голове."""
    return int(step_by_slug(slug)["id"])


def code(step_id: int) -> str:
    return step(step_id)["code"]


def qstep(qid: str) -> int:
    """Номер шага, где живёт вопрос. Идентификаторы вопросов уникальны на весь маршрут."""
    for s in steps():
        if any(q["id"] == qid for q in s["questions"]):
            return int(s["id"])
    raise KeyError(f"нет вопроса {qid}")


def segments(step_id: int) -> list[dict]:
    """Отрезки шага; у шага без отрезков — один отрезок из всех вопросов."""
    s = step(step_id)
    return s.get("segments") or [{"title": "", "questions": [q["id"] for q in s["questions"]]}]


def segment_index(step_id: int, qid: str) -> int:
    """Номер отрезка вопроса, с 1."""
    for i, seg in enumerate(segments(step_id), 1):
        if qid in seg["questions"]:
            return i
    return 1


def no_numbers(step_id: int, qid: str | None = None) -> bool:
    """Правило «без чисел»: весь шаг уровня «набросок» или указанный отрезок шага (no_numbers_segment)."""
    s = step(step_id)
    if s.get("level") == "sketch":
        return True
    seg = s.get("no_numbers_segment")
    if not seg or qid is None:  # без вопроса отрезок не известен — правило отрезка не применяется
        return False
    return segment_index(step_id, qid) == int(seg)


def migration() -> dict:
    return _load("steps.yaml").get("migration") or {}


def knowledge(step_id: int | None = None) -> list[dict]:
    """База знаний наставника (core/knowledge.yaml). Нет файла — пустой список."""
    if not (CORE_DIR / "knowledge.yaml").exists():
        return []
    out = _load("knowledge.yaml").get("advice") or []
    return [a for a in out if step_id is None or int(a.get("step", 0)) == int(step_id)]


def phase_of(step_id: int) -> dict:
    for p in phases():
        if int(step_id) in p["steps"]:
            return p
    return {}


def patterns() -> list[dict]:
    return _load("patterns.yaml")["patterns"]


def attacks() -> list[dict]:
    return _load("attacks.yaml")["attacks"]


def hints() -> list[dict]:
    return _load("hints.yaml")["hints"]


def senior_rules() -> dict:
    return _load("senior.yaml")


def domains() -> list[dict]:
    """Отраслевые обезличенные примеры (core/domains.yaml). Нет файла — пустой список, продукт работает без них."""
    if not (CORE_DIR / "domains.yaml").exists():
        return []
    out = _load("domains.yaml").get("domains") or []
    for d in out:
        d["steps"] = {int(k): v for k, v in (d.get("steps") or {}).items()}
        d.pop("source", None)  # откуда собраны примеры — закрытая информация, на экран не уходит
    return out


def domain(domain_id: str | None) -> dict | None:
    return next((d for d in domains() if d["id"] == domain_id), None)


def domain_examples(domain_id: str | None, step_id: int) -> list[str]:
    d = domain(domain_id)
    return list(d["steps"].get(int(step_id), [])) if d else []
