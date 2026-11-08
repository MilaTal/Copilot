"""Хранилище агента в SQLite: прогоны, журнал шагов (время, токены, цена), ключи идемпотентности, ручная очередь."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

SCHEMA = """
create table if not exists runs (id integer primary key, started text, query text, result text);
create table if not exists trace (run_id integer, step integer, kind text, name text, tokens_in integer,
  tokens_out integer, cost_usd real, note text, ts text);
create table if not exists actions (key text primary key, run_id integer, ts text);
create table if not exists requests (key text primary key, answer text, ts text);
create table if not exists queue (id integer primary key, run_id integer, status text, reason text, query text,
  created text, resolved text);
"""


def _now():
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def _x(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    def start_run(self, query):
        return self._x("insert into runs(started, query) values (?, ?)", (_now(), query[:5000])).lastrowid

    def finish_run(self, run_id, result: dict):
        self._x("update runs set result=? where id=?", (json.dumps(result, ensure_ascii=False), run_id))

    def trace(self, run_id, step, kind, name, tin, tout, cost, note):
        self._x("insert into trace values (?,?,?,?,?,?,?,?,?)", (run_id, step, kind, name, tin, tout, cost, note, _now()))

    def seen_action(self, key):
        return self._x("select 1 from actions where key=?", (key,)).fetchone() is not None

    def mark_action(self, key, run_id):
        self._x("insert or ignore into actions values (?,?,?)", (key, run_id, _now()))

    def get_request(self, key):
        row = self._x("select answer from requests where key=?", (key,)).fetchone()
        return row[0] if row else None

    def save_request(self, key, answer):
        self._x("insert or ignore into requests values (?,?,?)", (key, answer, _now()))

    def enqueue(self, run_id, status, reason, query):
        self._x("insert into queue(run_id, status, reason, query, created) values (?,?,?,?,?)",
                (run_id, status, reason, query[:2000], _now()))

    def open_queue(self):
        rows = self._x("select id, status, reason, created from queue where resolved is null order by id").fetchall()
        return [{"id": r[0], "status": r[1], "reason": r[2], "created": r[3]} for r in rows]

    def oldest_open_age_minutes(self):
        row = self._x("select min(created) from queue where resolved is null").fetchone()
        if not row or not row[0]:
            return 0
        return int((datetime.now() - datetime.fromisoformat(row[0])).total_seconds() // 60)
