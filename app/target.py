"""Вызов агента Junior Pilot по контракту «запрос → ответ». Общий для проверок качества и испытаний.

Токен доступа к агенту берётся только из переменной с префиксом AGENT_ и никогда не совпадает с ключами моделей:
иначе адрес агента превратился бы в канал утечки любого секрета сервера на любой хост."""
from __future__ import annotations

import json
import os
import re
import time
from urllib.parse import urlparse

import httpx

DEFAULT = {"method": "POST", "url": "", "input_field": "query", "output_field": "answer", "status_field": "status",
           "cost_field": "cost_usd", "key_field": "key", "headers_env": "", "timeout_sec": 300}
FIELDS = set(DEFAULT)
TOKEN_ENV_RE = re.compile(r"^AGENT_[A-Z0-9_]{1,60}$")
MAX_RESPONSE_BYTES = 2_000_000


def _model_key_envs() -> set[str]:
    try:
        from .router import load_models
        return {ep.get("key_env") for ep in load_models()["endpoints"].values() if ep.get("key_env")}
    except Exception:  # noqa: BLE001 — при битом файле роутера просто нет запрещённых имён сверх правила префикса
        return set()


def normalize(target: dict) -> dict:
    if not isinstance(target, dict):
        raise ValueError("адрес агента — набор полей")
    t = {**DEFAULT, **{k: v for k, v in target.items() if k in FIELDS and v not in (None, "")}}
    for k in ("method", "url", "input_field", "output_field", "status_field", "cost_field", "key_field", "headers_env"):
        t[k] = str(t[k]).strip()
    t["method"] = t["method"].upper()
    if t["method"] not in ("POST", "GET"):
        raise ValueError("метод — POST или GET")
    u = urlparse(t["url"])
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("адрес агента — http(s)://хост:порт/путь")
    if t["headers_env"]:
        if not TOKEN_ENV_RE.match(t["headers_env"]):
            raise ValueError("переменная с токеном агента — только с префиксом AGENT_, например AGENT_TOKEN")
        if t["headers_env"] in _model_key_envs():
            raise ValueError("ключ модели не может быть токеном агента")
    try:
        t["timeout_sec"] = max(5, min(int(t["timeout_sec"]), 900))
    except (TypeError, ValueError):
        raise ValueError("таймаут — число секунд")
    return t


def host_label(target: dict) -> str:
    u = urlparse(target["url"])
    return f"{u.hostname}:{u.port}" if u.port else (u.hostname or "")


def call(target: dict, query: str, key: str | None = None, transport: httpx.BaseTransport | None = None) -> dict:
    t = normalize(target)
    headers = {}
    if t.get("headers_env"):
        val = os.environ.get(t["headers_env"], "")
        if val:
            headers["Authorization"] = f"Bearer {val}"
    payload = {t["input_field"]: query}
    if key:
        payload[t["key_field"]] = key
    t0 = time.monotonic()
    body = b""
    truncated = False
    try:
        with httpx.Client(timeout=t["timeout_sec"], transport=transport) as c:
            with c.stream(t["method"], t["url"], json=payload if t["method"] == "POST" else None,
                          params=payload if t["method"] == "GET" else None, headers=headers) as r:
                status_code = r.status_code
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_RESPONSE_BYTES:
                        truncated = True
                        break
    except httpx.HTTPError as e:
        return {"ok": False, "status_code": None, "seconds": round(time.monotonic() - t0, 2),
                "answer": "", "status": "", "cost_usd": None, "error": f"{type(e).__name__}: {e}"[:300], "raw": ""}
    sec = round(time.monotonic() - t0, 2)
    text = body.decode("utf-8", "replace")
    raw = text[:4000]
    answer, status, cost = raw, "", None
    if not truncated:
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                answer = str(data.get(t["output_field"], "") or "")
                status = str(data.get(t["status_field"], "") or "")
                reason = data.get("reason")
                if reason and str(reason) not in answer:
                    answer = (answer + " " + str(reason)).strip()
                c_ = data.get(t["cost_field"])
                cost = float(c_) if c_ is not None else None
        except (ValueError, TypeError):
            pass
    return {"ok": status_code < 400 and not truncated, "status_code": status_code, "seconds": sec,
            "answer": answer[:4000], "status": status, "cost_usd": cost,
            "error": f"ответ агента больше {MAX_RESPONSE_BYTES // 1_000_000} МБ — прочитано начало" if truncated else "",
            "raw": raw}
