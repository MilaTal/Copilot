"""Прогон набора атак: каждая атака ловится, ни один обычный вопрос не закрыт ложной тревогой."""
from pathlib import Path

import pytest
import yaml

from app import guard

CORPUS = yaml.safe_load((Path(__file__).parent / "attack_corpus.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("text", CORPUS["provenance"])
def test_provenance_attack_closed(text):
    assert guard.is_provenance_question(text), text


@pytest.mark.parametrize("text", CORPUS["injection"])
def test_injection_detected(text):
    assert guard.detect_injection(text), text


@pytest.mark.parametrize("text", CORPUS["benign"])
def test_benign_not_flagged(text):
    assert not guard.is_provenance_question(text), ("ложно закрыт", text)
    assert not guard.detect_injection(text), ("ложная инъекция", text)
