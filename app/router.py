"""Роутер моделей: роль → упорядоченный список точек, запасная точка при отказе, журнал цены, потолок расходов.

Точки kind=openai — любой сервер, совместимый с OpenAI Chat Completions (OpenRouter, Ollama, LM Studio, vLLM).
Точки kind=anthropic — прямой API Anthropic через официальный SDK.
Ответ читается потоком: зависанием считается молчание между частями ответа, а не общая длительность."""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from . import config


class RouterError(Exception):
    """Ни одна точка роли не ответила. Текст — для человека, без ключей."""

    def __init__(self, msg: str = "", retryable: bool = False):
        super().__init__(msg)
        self.retryable = retryable  # разовый сетевой сбой: 429, 5xx, обрыв соединения


RETRY_PAUSE_SEC = 2.0


class BudgetExceeded(RouterError):
    pass


@dataclass
class ChatResult:
    text: str
    endpoint: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    seconds: float
    truncated: bool = False  # ответ упёрся в max_tokens


class ModelsConfigError(RouterError):
    """Файл роутера не читается. Экран «Модели» показывает текст файла, чтобы его можно было починить."""


TOP_LEVEL_FIELDS = {"budget_usd", "timeout_sec", "idle_timeout_sec", "endpoints", "roles"}
ALLOWED_ENDPOINT_FIELDS = {"kind", "base_url", "model", "key_env", "price_in", "price_out", "price_checked", "family"}
NUM_LIMITS = {"budget_usd": (0.01, 100_000), "timeout_sec": (5, 3600), "idle_timeout_sec": (2, 3600)}


def load_models(path: Path | None = None) -> dict:
    path = path or config.models_file()
    try:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError, UnicodeDecodeError) as e:
        raise ModelsConfigError(f"файл роутера {path.name} не читается: {str(e).splitlines()[0][:200]}")
    if not isinstance(cfg, dict):
        raise ModelsConfigError(f"файл роутера {path.name}: ожидался словарь настроек")
    cfg.setdefault("endpoints", {})
    cfg.setdefault("roles", {})
    cfg.setdefault("budget_usd", 10.0)
    cfg.setdefault("timeout_sec", 180)
    cfg.setdefault("idle_timeout_sec", 120)
    problems = validate_models(cfg)
    if problems:
        raise ModelsConfigError("файл роутера с ошибками: " + "; ".join(problems))
    return cfg


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def validate_models(cfg: dict) -> list[str]:
    """Проверка файла и сохранения с экрана. Возвращает список проблем словами."""
    if not isinstance(cfg, dict):
        return ["Настройки — словарь."]
    problems = []
    extra_top = set(cfg) - TOP_LEVEL_FIELDS
    if extra_top:
        problems.append(f"лишние поля {sorted(extra_top)} — ключи в файл не пишутся, только имя переменной в key_env")
    for k, (lo, hi) in NUM_LIMITS.items():
        x = _num(cfg.get(k))
        if x is None or not lo <= x <= hi:
            problems.append(f"{k} — число от {lo} до {hi}")
    eps = cfg.get("endpoints")
    if not isinstance(eps, dict) or not eps:
        problems.append("нет ни одной точки")
        eps = {}
    for name, ep in eps.items():
        if not isinstance(ep, dict):
            problems.append(f"{name}: описание точки — словарь полей")
            continue
        extra = set(ep) - ALLOWED_ENDPOINT_FIELDS
        if extra:
            problems.append(f"{name}: лишние поля {sorted(extra)} — ключи в файл не пишутся, только имя переменной в key_env")
        if ep.get("kind") not in ("openai", "anthropic"):
            problems.append(f"{name}: kind — openai или anthropic")
        url = str(ep.get("base_url") or "")
        if not url.startswith(("http://", "https://")):
            problems.append(f"{name}: base_url — адрес http:// или https://")
        if not ep.get("model"):
            problems.append(f"{name}: нужна model")
        for pk in ("price_in", "price_out"):
            x = _num(ep.get(pk, 0))
            if x is None or not 0 <= x <= 1000:
                problems.append(f"{name}: {pk} — число от 0 до 1000")
    roles = cfg.get("roles")
    if not isinstance(roles, dict):
        problems.append("roles — словарь «роль: список точек»")
        roles = {}
    for role, names in roles.items():
        if not isinstance(names, list):
            problems.append(f"роль {role}: список точек")
            continue
        for n in names:
            if n not in eps:
                problems.append(f"роль {role}: точки {n} нет в списке")
    return problems


HEADER = """# Роутер моделей. Файл правится здесь или на экране «Модели» — экран пишет сюда же и переписывает комментарии этим заголовком.
# Ключи сюда не пишутся: только имя переменной окружения в key_env, сами ключи — в .env.local.
# kind: openai    — любой сервер, совместимый с OpenAI Chat Completions: OpenRouter, Ollama, LM Studio, vLLM, SGLang, llama.cpp server.
# kind: anthropic — прямой API Anthropic через официальный SDK (pip install anthropic).
# Первая точка роли — основная, остальные — запасные по порядку.
# Цены — долларов за 1 млн токенов, нужны для журнала расходов и потолка; price_checked — дата сверки с прайсом, дд.мм.гггг.
# budget_usd — потолок расходов за месяц: при 90 % экран предупреждает, при 100 % вызовы останавливаются.
# timeout_sec — общий потолок одного вызова; idle_timeout_sec — молчание сервера дольше этого считается зависанием.
# Роли: senior — Senior Pilot; verifier и judge — другого семейства, чем Senior Pilot; reviewer — разбор кода Junior Pilot
# (без флажка «материалы можно в облако» роутер берёт только локальные точки этой роли).
"""


def save_models(cfg: dict, path: Path | None = None) -> None:
    problems = validate_models(cfg)
    if problems:
        raise ValueError("; ".join(problems))
    path = path or config.models_file()
    clean = {k: cfg[k] for k in ("budget_usd", "timeout_sec", "idle_timeout_sec") if k in cfg}
    clean["endpoints"] = {n: {k: v for k, v in ep.items() if k in ALLOWED_ENDPOINT_FIELDS} for n, ep in cfg["endpoints"].items()}
    clean["roles"] = cfg["roles"]
    tmp = path.with_suffix(".yaml.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(HEADER)
        yaml.safe_dump(clean, f, allow_unicode=True, sort_keys=False)
    tmp.replace(path)


def public_view(cfg: dict) -> dict:
    """Конфигурация для экрана: только разрешённые поля; вместо ключа — признак, задан ли он."""
    out = {k: cfg.get(k) for k in ("budget_usd", "timeout_sec", "idle_timeout_sec")}
    out["roles"] = cfg.get("roles") or {}
    out["endpoints"] = {}
    for name, ep in (cfg.get("endpoints") or {}).items():
        view = {k: v for k, v in ep.items() if k in ALLOWED_ENDPOINT_FIELDS}
        key_env = ep.get("key_env") or ""
        view["key_present"] = bool(key_env and os.environ.get(key_env))
        view["key_needed"] = bool(key_env)
        out["endpoints"][name] = view
    return out


def month_spent(log_path: Path | None = None) -> float:
    log_path = log_path or config.cost_log()
    if not log_path.exists():
        return 0.0
    month = datetime.now().strftime("%Y-%m")
    total = 0.0
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(rec.get("ts", "")).startswith(month):
            total += float(rec.get("cost_usd") or 0)
    return round(total, 6)


def recent_calls(limit: int = 50) -> list[dict]:
    path = config.cost_log()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


class Router:
    def __init__(self, cfg: dict | None = None, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg or load_models()
        self.transport = transport

    # ---------- публичное ----------
    def budget_state(self) -> dict:
        spent = month_spent() + _reserved_total()
        budget = float(self.cfg.get("budget_usd") or 0)
        share = spent / budget if budget else 1.0
        return {"spent_usd": round(spent, 4), "budget_usd": budget, "share": round(share, 4),
                "warn": share >= 0.9, "stopped": share >= 1.0}

    def chat(self, role: str, system: str, messages: list[dict], *, max_tokens: int = 4000,
             temperature: float = 0.2, purpose: str = "", only_local: bool = False) -> ChatResult:
        """only_local=True — материалы Junior Pilot без флажка «можно в облако»: пропускаются точки за периметром."""
        state = self.budget_state()
        if state["stopped"]:
            raise BudgetExceeded(
                f"Потолок расходов исчерпан: {state['spent_usd']} $ из {state['budget_usd']} $ за месяц. "
                "Поднимите потолок на экране «Модели» или дождитесь нового месяца.")
        names = self.cfg["roles"].get(role) or []
        if not names:
            raise RouterError(f"Для роли «{role}» не назначено ни одной точки на экране «Модели».")
        errors = []
        deadline = time.monotonic() + float(self.cfg.get("timeout_sec") or 180)
        for name in names:
            if time.monotonic() > deadline:
                errors.append("исчерпан общий потолок времени вызова")
                break
            ep = self.cfg["endpoints"].get(name)
            if not ep:
                errors.append(f"{name}: точка не описана")
                continue
            if only_local and not is_local_url(ep.get("base_url", "")):
                errors.append(f"{name}: за периметром, а материалы проекта разрешены только локальной модели")
                continue
            reserve = self._estimate(ep, system, messages, max_tokens)
            if not _reserve(reserve, float(self.cfg.get("budget_usd") or 0)):
                raise BudgetExceeded("Потолок расходов исчерпан с учётом вызовов, которые уже идут. "
                                     "Поднимите потолок на экране «Модели» или дождитесь нового месяца.")
            try:
                try:
                    result = self._call(name, ep, system, messages, max_tokens, temperature, deadline)
                except RouterError as e:
                    # разовый сбой провайдера — один повтор на той же точке, затем запасная
                    if not e.retryable or time.monotonic() + RETRY_PAUSE_SEC > deadline:
                        raise
                    time.sleep(RETRY_PAUSE_SEC)
                    result = self._call(name, ep, system, messages, max_tokens, temperature, deadline)
            except RouterError as e:
                errors.append(f"{name}: {e}")
                continue
            finally:
                _release(reserve)
            self._log(role, purpose, result, ok=True)
            return result
        self._log_failure(role, purpose, errors)
        raise RouterError("Ни одна точка роли «%s» не ответила. %s" % (role, " | ".join(errors)))

    def check(self, name: str) -> dict:
        """Короткий вызов для кнопки «Проверить». Тратит несколько токенов."""
        ep = self.cfg["endpoints"].get(name)
        if not ep:
            return {"ok": False, "error": "точка не описана"}
        try:
            r = self._call(name, ep, "Ответь одним словом.", [{"role": "user", "content": "Скажи: готов"}], 16, 0)
        except RouterError as e:
            return {"ok": False, "error": str(e)}
        self._log("check", "проверка точки", r, ok=True)
        return {"ok": True, "answer": r.text[:80], "seconds": r.seconds, "model": r.model}

    # ---------- внутреннее ----------
    def _key(self, ep: dict) -> str:
        key_env = ep.get("key_env") or ""
        if not key_env:
            return ""
        key = os.environ.get(key_env, "")
        if not key:
            raise RouterError(f"не задан ключ: переменная {key_env} отсутствует в .env.local")
        return key

    def _call(self, name, ep, system, messages, max_tokens, temperature, deadline=None) -> ChatResult:
        if ep.get("kind") == "anthropic":
            return self._call_anthropic(name, ep, system, messages, max_tokens)
        return self._call_openai(name, ep, system, messages, max_tokens, temperature, deadline)

    def _estimate(self, ep, system, messages, max_tokens) -> float:
        tin = _estimate_tokens(system) + sum(_estimate_tokens(m.get("content", "")) for m in messages)
        return self._cost(ep, tin, max_tokens)

    def _cost(self, ep: dict, tin: int, tout: int) -> float:
        return round(tin / 1e6 * float(ep.get("price_in") or 0) + tout / 1e6 * float(ep.get("price_out") or 0), 6)

    def _call_openai(self, name, ep, system, messages, max_tokens, temperature, deadline=None) -> ChatResult:
        key = self._key(ep)
        url = ep["base_url"].rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        body = {
            "model": ep["model"],
            "messages": [{"role": "system", "content": system}] + messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        idle = float(self.cfg.get("idle_timeout_sec") or 120)
        total_limit = float(self.cfg.get("timeout_sec") or 180)
        t0 = time.monotonic()
        stop_at = min(t0 + total_limit, deadline or float("inf"))
        timeout = httpx.Timeout(connect=10.0, read=idle, write=30.0, pool=10.0)
        parts: list[str] = []
        usage: dict = {}
        finished = False
        cut_by_length = False
        buf = b""

        def handle(line: bytes):
            nonlocal finished, usage, cut_by_length
            line = line.strip()
            if not line.startswith(b"data:"):
                return
            data = line[5:].strip()
            if data == b"[DONE]":
                finished = True
                return
            try:
                chunk = json.loads(data.decode("utf-8", "replace"), strict=False)
            except json.JSONDecodeError:
                return
            if chunk.get("error"):
                err = chunk["error"]
                code = err.get("code") if isinstance(err, dict) else None
                raise RouterError(str(err)[:200], retryable=isinstance(code, int) and (code == 429 or code >= 500))
            for ch in chunk.get("choices") or []:
                delta = ch.get("delta") or {}
                if delta.get("content"):
                    parts.append(delta["content"])
                if ch.get("finish_reason"):
                    finished = True
                    cut_by_length = ch["finish_reason"] == "length"
            if chunk.get("usage"):
                usage = chunk["usage"]

        try:
            with httpx.Client(timeout=timeout, transport=self.transport) as client:
                with client.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code >= 400:
                        resp.read()
                        raise RouterError(f"HTTP {resp.status_code}: {resp.text[:200]}",
                                          retryable=resp.status_code == 429 or resp.status_code >= 500)
                    for piece in resp.iter_bytes():
                        if time.monotonic() > stop_at:
                            raise RouterError(f"превышен общий потолок вызова {total_limit:.0f} с")
                        buf += piece
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            handle(line)
                        if len(buf) > 2_000_000:
                            raise RouterError("поток без разделителей строк — ответ не похож на поток модели")
                    if buf:
                        handle(buf)
        except httpx.ReadTimeout:
            raise RouterError(f"сервер молчит дольше {idle:.0f} с — похоже на зависание")
        except httpx.HTTPError as e:
            raise RouterError(f"сеть: {type(e).__name__}: {e}", retryable=True)
        text = "".join(parts)
        if not text.strip():
            raise RouterError("пустой ответ модели")
        if not finished:
            raise RouterError("ответ модели оборван — поток закончился без признака конца")
        tin = int(usage.get("prompt_tokens") or _estimate_tokens(system) + sum(_estimate_tokens(m["content"]) for m in messages))
        tout = int(usage.get("completion_tokens") or _estimate_tokens(text))
        return ChatResult(text, name, ep["model"], tin, tout, self._cost(ep, tin, tout), round(time.monotonic() - t0, 2),
                          truncated=cut_by_length)

    def _call_anthropic(self, name, ep, system, messages, max_tokens) -> ChatResult:
        try:
            import anthropic  # официальный SDK, ставится отдельно: pip install anthropic
        except ImportError:
            raise RouterError("для kind: anthropic нужен пакет anthropic (pip install anthropic)")
        key = self._key(ep)
        client = anthropic.Anthropic(api_key=key, base_url=ep.get("base_url") or None,
                                     timeout=float(self.cfg.get("timeout_sec") or 180))
        t0 = time.monotonic()
        try:
            resp = client.messages.create(
                model=ep["model"],
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                output_config={"effort": "high"},
                # Серверный запасной путь при отказе по классификатору безопасности.
                extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
                extra_body={"fallbacks": "default"},
            )
        except anthropic.APIStatusError as e:
            raise RouterError(f"HTTP {e.status_code}: {str(e)[:200]}")
        except anthropic.APIConnectionError as e:
            raise RouterError(f"сеть: {e}")
        if resp.stop_reason == "refusal":
            raise RouterError("модель отказалась отвечать (refusal)")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        tin, tout = resp.usage.input_tokens, resp.usage.output_tokens
        return ChatResult(text, name, ep["model"], tin, tout, self._cost(ep, tin, tout), round(time.monotonic() - t0, 2))

    def _log(self, role, purpose, r: ChatResult, ok: bool):
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "role": role, "purpose": purpose,
               "endpoint": r.endpoint, "model": r.model, "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
               "cost_usd": r.cost_usd, "seconds": r.seconds, "ok": ok}
        with open(config.cost_log(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _log_failure(self, role, purpose, errors):
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "role": role, "purpose": purpose,
               "endpoint": None, "cost_usd": 0, "ok": False, "error": " | ".join(errors)[:500]}
        with open(config.cost_log(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


_RESERVED = [0.0]
_RES_LOCK = threading.Lock()


def _reserved_total() -> float:
    with _RES_LOCK:
        return _RESERVED[0]


def _reserve(amount: float, budget: float) -> bool:
    """Резерв оценки стоимости на время вызова: параллельные вызовы не проходят сверх потолка."""
    with _RES_LOCK:
        if budget and month_spent() + _RESERVED[0] + amount > budget * 1.0 and amount > 0:
            return False
        _RESERVED[0] += amount
        return True


def _release(amount: float) -> None:
    with _RES_LOCK:
        _RESERVED[0] = max(0.0, _RESERVED[0] - amount)


def is_local_url(url: str) -> bool:
    """Точка внутри периметра: localhost, loopback, частные сети, имена *.local / *.lan / *.internal."""
    import ipaddress
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost",) or host.endswith((".local", ".lan", ".internal", ".localhost")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _estimate_tokens(text: Any) -> int:
    return max(1, int(len(str(text)) / 3.5))


def _repair_inner_quotes(s: str) -> str:
    """Экранирует прямую кавычку внутри строки, если за ней не идёт конец значения (`,` `:` `}` `]`).

    Модель пишет в образце ответа `шаг "сверка счёта"` вопреки просьбе ставить «ёлочки»; просьба в промпте — не замок."""
    out, in_str, esc, i = [], False, False, 0
    while i < len(s):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                j = i + 1
                while j < len(s) and s[j] in " \t\r\n":
                    j += 1
                closing = j >= len(s) or s[j] in ":}]"
                if j < len(s) and s[j] == ",":
                    k = j + 1
                    while k < len(s) and s[k] in " \t\r\n":
                        k += 1
                    closing = k >= len(s) or s[k] in '"}]'  # «, режим тень» — запятая внутри текста
                if not closing:
                    out.append('\\"')
                    i += 1
                    continue
                in_str = False
        elif c == '"':
            in_str = True
        out.append(c)
        i += 1
    return "".join(out)


def extract_json(text: str) -> Any:
    """Достаёт первый JSON-объект из ответа модели. strict=False — в строки попадают табуляции из исходных текстов.
    Если разбор не удался — одна попытка починить прямые кавычки внутри строк."""
    try:
        return _extract_json(text)
    except json.JSONDecodeError:
        return _extract_json(_repair_inner_quotes(text))


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    if start < 0:
        raise ValueError("в ответе модели нет JSON")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1], strict=False)
    raise ValueError("JSON в ответе модели оборван")
