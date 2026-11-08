"""Отраслевые примеры: 8 отраслей × 6 шагов, обезличены, видны Senior Pilot и экрану."""
import re

from fastapi.testclient import TestClient

from app import core, senior, state
from app.main import app

BRANDS = re.compile(r"(?i)\b(walmart|pactum|scoutbee|accio|hackett|metr|gartner|navigant|procore|buildots|icertis|harvey|"
                    r"luminance|spellbook|еаптека|аптека\.ру|directum|teamly|paligo|docboss|echoleak|microsoft|amazon|сбер|"
                    r"яндекс|нейроюрист|напарник|vanessa|cursor|chatgpt|gpt-4|metacode|хабр|vc\.ru|коммерсант)\b")


def test_eight_domains_six_steps():
    """Было: 7 отраслей × 12 шагов по 1–2 примера. Стало: 8 отраслей × 6 шагов по 1–3 примера."""
    ds = core.domains()
    assert [d["id"] for d in ds] == ["contracts", "inbox_rfq", "techdocs", "construction", "onec", "procurement",
                                     "agents", "decision_support"]
    for d in ds:
        assert sorted(d["steps"]) == core.step_ids(), d["id"]
        assert all(1 <= len(v) <= 3 for v in d["steps"].values())
        assert d["hints"] and all(core.has_step(h["step"]) for h in d["hints"])
    assert all(core.has_step(h["step"]) for h in core.hints())


def test_domains_anonymized():
    text = (core.CORE_DIR / "domains.yaml").read_text(encoding="utf-8")
    m = BRANDS.search(text)
    assert not m, m and m.group()
    assert not re.search(r"\[\d+\]", text), "ссылки на источники должны быть убраны"


def test_project_domain_feeds_senior_and_screen():
    st = state.create("Сверка счетов", 1, domain="procurement")
    block = senior.domain_block(st, core.sid("architecture"))
    assert "Закупки" in block and "не факты этого проекта" in block
    assert senior.domain_block(st, core.sid("problem")) == ""  # набросок — без чужих чисел
    assert senior.domain_block(st, core.sid("solution"), "core_step") == ""  # отрезок «без чисел»
    assert senior.domain_block(st, core.sid("solution"), "kpi_table")
    c = TestClient(app)
    assert len(c.get("/api/meta").json()["domains"]) == 8
    assert c.post("/api/projects", json={"name": "x", "domain": "нет-такой"}).status_code == 400
