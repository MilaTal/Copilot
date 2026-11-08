"""Адрес агента для проверок качества и испытаний: POST /run {"query": "...", "key": "..."} → исход прогона.

Запуск только на стенде: uvicorn server:app --host 127.0.0.1 --port 8100
Порт наружу — решение, а не умолчание: без --host сервер слушает только локальный адрес."""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from agent import Agent

app = FastAPI(title="Агент — стенд")
_agent = Agent()


class RunIn(BaseModel):
    query: str = ""
    key: str | None = None


@app.post("/run")
def run(body: RunIn):
    res = _agent.run(body.query, request_key=body.key)
    return res.as_dict()


@app.get("/queue")
def queue():
    return {"open": _agent.store.open_queue(), "oldest_minutes": _agent.store.oldest_open_age_minutes()}
