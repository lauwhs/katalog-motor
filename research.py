"""
Riset tiap motor di web: spesifikasi lengkap + poin kelebihan/kekurangan dari ulasan media -> data/research.json

- Claude mencari artikel tentang motor tersebut, lalu merangkum FAKTA dengan kata-katanya sendiri
  (tidak menyalin kalimat artikel) dan menyertakan alamat sumber tiap data.
- Hanya motor yang belum diriset yang diproses. Ulang semua: python research.py --refresh
"""
import json, os, re, sys, time
from datetime import date
from pathlib import Path
import anthropic

MODEL = os.getenv("RESEARCH_MODEL", "claude-haiku-5-5")
ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "research.json"
SKIP = {"motor.json", "overrides.json", "enriched.json", "research.json", "articles.json", "commons.json"}
SPEC_LABELS = {"tenaga": "Tenaga", "torsi": "Torsi", "bobot": "Bobot", "tinggi_jok": "Tinggi jok",
               "tangki": "Tangki BBM", "transmisi": "Transmisi", "rem": "Rem", "suspensi": "Suspensi",
               "ban": "Ban", "fitur": "Fitur"}

PROMPT = """Cari di web artikel dan halaman resmi tentang motor {brand} {name} yang dijual resmi di Indonesia (cc: {cc}, mesin: {engine}, kategori: {body}).

Tugas:
A. Kumpulkan spesifikasi: tenaga, torsi, bobot (kg), tinggi jok (mm), kapasitas tangki BBM (liter), transmisi, rem, suspensi, ban, dan fitur utama (daftar singkat dipisah koma).
B. Temukan sampai 3 ulasan media otomotif Indonesia (misalnya GridOto, Otomotif Net, Autofun, Motorplus, Carmudi, Oto Detik, Moladin) lalu ringkas poin kelebihan dan kekurangan yang disebut ulasan tersebut.

Aturan ketat:
1. Pakai hanya fakta yang TERTULIS di sumber. Jangan menebak atau memakai angka motor lain. Utamakan situs resmi Kawasaki untuk spesifikasi.
2. Tulis ulang dengan kata-katamu sendiri. Jangan menyalin kalimat dari artikel. Setiap poin pendek (maksimal sekitar 15 kata).
3. Jika tidak ditemukan, isi null. Setiap spesifikasi wajib punya URL sumber.
4. Untuk motor dengan model tahun berbeda, pastikan datanya untuk model yang dijual saat ini.

Balas HANYA dengan JSON:
{{"specs": {{"tenaga": {{"value": null, "source": null}}, "torsi": {{"value": null, "source": null}}, "bobot": {{"value": null, "source": null}},
"tinggi_jok": {{"value": null, "source": null}}, "tangki": {{"value": null, "source": null}}, "transmisi": {{"value": null, "source": null}},
"rem": {{"value": null, "source": null}}, "suspensi": {{"value": null, "source": null}}, "ban": {{"value": null, "source": null}},
"fitur": {{"value": null, "source": null}}}},
"ulasan": [{{"media": "nama media", "url": "https://...", "kelebihan": ["..."], "kekurangan": ["..."]}}]}}"""


def ask(client, m):
    msgs = [{"role": "user", "content": PROMPT.format(**{k: m.get(k) for k in ("brand", "name", "cc", "engine", "body")})}]
    for _ in range(4):
        r = client.messages.create(model=MODEL, max_tokens=6000, messages=msgs,
                                   tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}])
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
    done = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    motors = []
    for f in sorted(DATA.glob("*.json")):
        if f.name not in SKIP and not f.name.startswith("."):
            motors += json.loads(f.read_text(encoding="utf-8"))
    todo = [m for m in motors if refresh or m["id"] not in done]
    print(f"{len(todo)} motor perlu diriset (dari {len(motors)})")
    for i, m in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {m['brand']} {m['name']}")
        try:
            res = ask(client, m)
        except Exception as e:
            print("   GAGAL:", e)
            continue
        specs = {}
        for k, v in (res.get("specs") or {}).items():
            if k in SPEC_LABELS and isinstance(v, dict) and v.get("value") and v.get("source"):
                specs[k] = {"value": str(v["value"]), "source": v["source"]}
        reviews = []
        for u in (res.get("ulasan") or [])[:3]:
            if u.get("url") and (u.get("kelebihan") or u.get("kekurangan")):
                reviews.append({"media": str(u.get("media") or "Media otomotif"), "url": u["url"],
                                "kelebihan": [str(x) for x in (u.get("kelebihan") or [])][:4],
                                "kekurangan": [str(x) for x in (u.get("kekurangan") or [])][:4]})
        done[m["id"]] = {"specs": specs, "ulasan": reviews, "dicek": date.today().isoformat()}
        OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"   spesifikasi: {len(specs)}, ulasan: {len(reviews)}")
        time.sleep(1)


if __name__ == "__main__":
    main()
