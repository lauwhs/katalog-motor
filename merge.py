"""Gabungkan data semua merek (data/<merek>.json) menjadi data/motor.json untuk website."""
import json
from pathlib import Path

d = Path(__file__).parent / "data"
semua = []
for f in sorted(d.glob("*.json")):
    if f.name in ("motor.json", "overrides.json") or f.name.startswith("."):
        continue
    semua += json.loads(f.read_text(encoding="utf-8"))
(d / "motor.json").write_text(json.dumps(semua, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"motor.json: {len(semua)} motor")
