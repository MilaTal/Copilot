"""Калибровка Senior Pilot: прогон размеченного набора через ту же проверку ответа, что на экране.

Запуск: python -m calibration.run_calibration [метка]   (платно)
Пишет отчёт calibration/отчёт <дд.мм.гггг чч-мм> <метка>.md: совпадение с разметкой на явных случаях,
лишняя строгость (хороший ответ отправлен на уточнение), лишняя мягкость (размытый принят), пограничные — отдельно."""
from __future__ import annotations

import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def main(tag: str = "") -> dict:
    import os
    from app import config
    config.load_env_local()
    from app import core, senior, state
    from app.router import Router, RouterError

    data = yaml.safe_load((HERE / "senior_set.yaml").read_text(encoding="utf-8"))
    router = Router()
    base = state.create(data["context"]["name"], 1)
    for step, qid, val in data["context"]["answers"]:
        state.set_answer(base, step, qid, "answered", val)

    def one(case):
        st = json.loads(json.dumps(base))
        state.set_answer(st, case["step"], case["qid"], "answered", case["answer"])
        try:
            r = senior.review_answer(st, case["step"], case["qid"], router=router)
            return {**case, "got": r["verdict"], "comment": r["comment"], "follow_up": r["follow_up"], "cost": r["cost_usd"]}
        except RouterError as e:
            return {**case, "got": "error", "comment": str(e), "follow_up": "", "cost": 0}

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(one, data["cases"]))
    clear = [r for r in results if not r.get("borderline") and r["got"] != "error"]
    agree = sum(1 for r in clear if r["got"] == r["label"])
    too_strict = [r for r in clear if r["label"] == "accepted" and r["got"] == "needs_detail"]
    too_soft = [r for r in clear if r["label"] == "needs_detail" and r["got"] == "accepted"]
    summary = {"tag": tag, "cases": len(results), "clear": len(clear), "agree": agree,
               "agreement_pct": round(agree / len(clear) * 100) if clear else 0,
               "too_strict": len(too_strict), "too_soft": len(too_soft),
               "errors": sum(1 for r in results if r["got"] == "error"),
               "cost_usd": round(sum(r["cost"] for r in results), 4)}
    now = datetime.now()
    lines = [f"# Калибровка Senior Pilot — {now.strftime('%d.%m.%Y %H:%M')} {tag}".rstrip(), "",
             f"Явных случаев {summary['clear']}, совпало с разметкой {agree} ({summary['agreement_pct']} %). "
             f"Лишняя строгость: {summary['too_strict']}. Лишняя мягкость: {summary['too_soft']}. "
             f"Ошибок вызова: {summary['errors']}. Стоимость прогона: {summary['cost_usd']} $.", "",
             "| шаг | вопрос | разметка | Senior Pilot | ответ Junior Pilot | комментарий |", "|---|---|---|---|---|---|"]
    for r in results:
        mark = "погран." if r.get("borderline") else r["label"]
        lines.append(f"| {core.code(r['step'])} | {r['qid']} | {mark} | {r['got']} | {r['answer'][:90]} | "
                     f"{(r['comment'] + (' Уточнить: ' + r['follow_up'] if r['follow_up'] else ''))[:220].replace('|', '/')} |")
    out = HERE / (f"отчёт {now.strftime('%d.%m.%Y %H-%M')} {tag}".rstrip() + ".md")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return summary


if __name__ == "__main__":
    from app import state
    with state.archived_test_run():  # проект прогона не остаётся в общем списке
        main(sys.argv[1] if len(sys.argv) > 1 else "")
