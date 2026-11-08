"""Замер защиты по корпусу закрытой информации: пропуски по темам и ложные закрытия. Запуск: python -m tests.measure_provenance"""
from pathlib import Path

import yaml

from app import guard

c = yaml.safe_load((Path(__file__).parent / "provenance_corpus.yaml").read_text(encoding="utf-8"))
total = missed = 0
for topic, qs in c["attacks"].items():
    miss = [q for q in qs if not guard.is_provenance_question(q)]
    total += len(qs); missed += len(miss)
    print(f"{topic:12} пропущено {len(miss)} из {len(qs)}")
    for q in miss:
        print("   -", q)
fp = [q for q in c["benign"] if guard.is_provenance_question(q)]
print(f"ИТОГО атак пропущено {missed} из {total}; ложно закрыто {len(fp)} из {len(c['benign'])}")
for q in fp:
    print("   +", q)
hm = [q for q in c.get("holdout_attacks", []) if not guard.is_provenance_question(q)]
hf = [q for q in c.get("holdout_benign", []) if guard.is_provenance_question(q)]
print(f"ОТЛОЖЕННЫЙ: атак пропущено {len(hm)} из {len(c.get('holdout_attacks', []))}; ложно закрыто {len(hf)} из {len(c.get('holdout_benign', []))}")
for q in hm:
    print("   -", q)
for q in hf:
    print("   +", q)
h2m = [q for q in c.get("holdout2_attacks", []) if not guard.is_provenance_question(q)]
h2f = [q for q in c.get("holdout2_benign", []) if guard.is_provenance_question(q)]
print(f"ОТЛОЖЕННЫЙ-2: атак пропущено {len(h2m)} из {len(c.get('holdout2_attacks', []))}; ложно закрыто {len(h2f)} из {len(c.get('holdout2_benign', []))}")
for q in h2m:
    print("   -", q)
for q in h2f:
    print("   +", q)
