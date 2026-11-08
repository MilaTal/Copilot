"""Клиент модели: любой сервер, совместимый с OpenAI Chat Completions, с запасными точками.

Повтор — только на сетевых отказах (429, 5xx, обрыв), с растущей задержкой. Плохой ответ модели не повторяется здесь:
это решает цикл агента."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx


class LLMError(Exception):
    pass


@dataclass
class Reply:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float


class LLM:
    def __init__(self, endpoints: list[dict], retries: int = 3, timeout: float = 120, transport=None):
        self.endpoints = endpoints
        self.retries = retries
        self.timeout = timeout
        self.transport = transport

    def chat(self, system: str, messages: list[dict]) -> Reply:
        errors = []
        for ep in self.endpoints:
            for attempt in range(self.retries + 1):
                try:
                    return self._call(ep, system, messages)
                except _Retryable as e:
                    errors.append(f"{ep['model']}: {e}")
                    if attempt < self.retries:
                        time.sleep(min(2 ** attempt, 8))
                except LLMError as e:
                    errors.append(f"{ep['model']}: {e}")
                    break
        raise LLMError("; ".join(errors)[-500:])

    def _call(self, ep, system, messages) -> Reply:
        headers = {}
        if ep.get("key_env"):
            key = os.environ.get(ep["key_env"], "")
            if not key:
                raise LLMError(f"не задан ключ {ep['key_env']}")
            headers["Authorization"] = f"Bearer {key}"
        body = {"model": ep["model"], "temperature": 0.1,
                "messages": [{"role": "system", "content": system}] + messages}
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as c:
                r = c.post(ep["base_url"].rstrip("/") + "/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as e:
            raise _Retryable(f"сеть: {e}")
        if r.status_code == 429 or r.status_code >= 500:
            raise _Retryable(f"HTTP {r.status_code}")
        if r.status_code >= 400:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        usage = data.get("usage") or {}
        tin, tout = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        cost = tin / 1e6 * float(ep.get("price_in", 0)) + tout / 1e6 * float(ep.get("price_out", 0))
        return Reply(data["choices"][0]["message"]["content"] or "", ep["model"], tin, tout, cost)


class _Retryable(LLMError):
    pass
