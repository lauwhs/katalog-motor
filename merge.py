"""Gabungkan data semua merek (data/<merek>.json) + data performa (enriched.json) menjadi data/motor.json."""
import json
from pathlib import Path

d = Path(__file__).parent / "data"
enriched = json.loads((d / "enriched.json").read_text(encoding="utf-8")) if (d / "enriched.json").exists() else {}
semua = []
for f in sorted(d.glob("*.json")):
    if f.name in ("motor.json", "overrides.json", "enriched.json") or f.name.startswith("."):
        continue
    semua += json.loads(f.read_text(encoding="utf-8"))
for m in semua:
    e = enriched.get(m["id"], {})
    for k in ("release", "speed", "kmpl", "acc", "range"):
        if m.get(k) is None and e.get(k) is not None:     # isian manual/resmi tidak ditimpa
            m[k] = e[k]
            m.setdefault("perf", {})[k] = e.get("perf", {}).get(k)
(d / "motor.json").write_text(json.dumps(semua, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"motor.json: {len(semua)} motor, {sum(1 for m in semua if m.get('perf'))} dengan data performa tambahan")
