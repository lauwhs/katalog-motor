"""
Lengkapi data performa (tanggal rilis di Indonesia, top speed, konsumsi BBM, akselerasi, jangkauan EV)
dengan meminta Claude mencari di web. Hasil + sumbernya disimpan di data/enriched.json.

- Hanya motor yang datanya masih kosong yang diproses, dan hasilnya disimpan (tidak dicari ulang tiap hari).
- Nilai tanpa sumber yang jelas diisi null. Isian manual di overrides.json selalu menang.
Jalankan:  python enrich.py            (hanya motor baru / yang belum diproses)
           python enrich.py --refresh  (cari ulang semua)
"""
import json, os, re, sys, time
from pathlib import Path
import anthropic

MODEL = os.getenv("ENRICH_MODEL", "claude-haiku-5-5")
FIELDS = ["release", "speed", "kmpl", "acc", "range"]
ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "enriched.json"
SKIP = {"motor.json", "overrides.json", "enriched.json", "research.json", "articles.json"}

PROMPT = """Cari di web data berikut untuk motor {brand} {name} yang dijual resmi di Indonesia (cc: {cc}, mesin: {engine}).

Data yang dicari:
- release: bulan dan tahun peluncuran resmi MODEL INI di Indonesia, format "YYYY-MM" (jika hanya tahun diketahui, pakai "YYYY-00")
- speed: top speed dalam km/jam
- kmpl: konsumsi BBM rata-rata dalam km per liter (isi null untuk motor listrik)
- acc: akselerasi 0-100 km/jam dalam detik
- range: jangkauan dalam km (hanya untuk motor listrik, selain itu null)

Aturan ketat:
1. Pakai hanya angka yang TERTULIS di sumber yang kamu temukan (uji media otomotif, rilis resmi, situs pabrikan). Jangan menebak, menghitung, atau memakai angka motor lain.
2. Jika tidak ditemukan atau sumbernya meragukan, isi null.
3. Untuk setiap angka, tulis basis: "uji" (hasil pengujian media), "klaim pabrikan", atau "berita" (untuk tanggal rilis), beserta URL sumbernya.
4. Prioritaskan sumber Indonesia untuk tanggal rilis dan konsumsi BBM.

Balas HANYA dengan satu objek JSON seperti ini (tanpa teks lain):
{{"release": {{"value": null, "basis": null, "source": null}}, "speed": {{"value": null, "basis": null, "source": null}},
"kmpl": {{"value": null, "basis": null, "source": null}}, "acc": {{"value": null, "basis": null, "source": null}},
"range": {{"value": null, "basis": null, "source": null}}}}"""


def ask(client, m):
    msgs = [{"role": "user", "content": PROMPT.format(**{k: m.get(k) for k in ("brand", "name", "cc", "engine")})}]
    for _ in range(4):   # lanjutkan bila pencarian dijeda
        r = client.messages.create(
            model=MODEL, max_tokens=4000, messages=msgs,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
        )
        if r.stop_reason != "pause_turn":
            break
        msgs.append({"role": "assistant", "content": r.content})
    text = "".join(b.text for b in r.content if b.type == "text")
    found = re.search(r"\{.*\}", text, re.S)
    return json.loads(found.group(0)) if found else {}


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY belum diatur")
    refresh = "--refresh" in sys.argv
    client = anthropic.Anthropic()
    enriched = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    motors = []
    for f in sorted(DATA.glob("*.json")):
        if f.name not in SKIP and not f.name.startswith("."):
            motors += json.loads(f.read_text(encoding="utf-8"))

    todo = [m for m in motors if (refresh or m["id"] not in enriched) and any(m.get(k) is None for k in FIELDS)]
    print(f"{len(todo)} motor perlu dilengkapi (dari {len(motors)})")
    for i, m in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {m['brand']} {m['name']}")
        try:
            res = ask(client, m)
        except Exception as e:
            print("   GAGAL:", e)
            continue
        entry, perf = {}, {}
        for k in FIELDS:
            v = res.get(k) or {}
            if v.get("value") is not None and v.get("source"):
                entry[k] = v["value"]
                perf[k] = {"basis": v.get("basis"), "source": v["source"]}
        entry["perf"] = perf
        enriched[m["id"]] = entry
        OUT.write_text(json.dumps(enriched, ensure_ascii=False, indent=1), encoding="utf-8")
        print("   terisi:", ", ".join(perf) or "tidak ada")
        time.sleep(1)


if __name__ == "__main__":
    main()
