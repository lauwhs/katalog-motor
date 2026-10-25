"""
Tulis artikel ulasan tiap motor dengan Claude -> data/articles.json

- Artikel disusun HANYA dari data di data/motor.json (spesifikasi resmi + data performa bersumber).
- Artikel ditulis ulang otomatis hanya jika data motornya berubah (dicek lewat hash).
Jalankan:  python merge.py && python write_articles.py
"""
import hashlib, json, os, re, sys, time
from pathlib import Path
import anthropic

MODEL = os.getenv("ARTICLE_MODEL", "claude-haiku-5-5")
ROOT = Path(__file__).parent
SRC = ROOT / "data" / "motor.json"
OUT = ROOT / "data" / "articles.json"
RESEARCH = ROOT / "data" / "research.json"
FIELDS = ["brand", "name", "body", "engine", "cc", "price", "variants", "model_year",
          "release", "speed", "kmpl", "acc", "range", "sum", "perf"]

PROMPT = """Tulis ulasan ringkas berbahasa Indonesia untuk motor di bawah ini, berdasarkan HANYA data berikut.

DATA (JSON):
{data}

Aturan ketat:
1. Boleh memakai spesifikasi di bagian "riset", tetapi jangan menambah spesifikasi apa pun yang tidak ada di data.
2. Jangan mengaku pernah mengendarai atau menguji motor ini, dan jangan mengarang kesan berkendara.
3. Jika suatu data kosong (null), tulis bahwa datanya belum tersedia. Jangan menebak.
4. Untuk data di kolom perf, sebut basisnya ("hasil uji", "klaim pabrikan", atau "berita") bila ada.
5. Harga adalah MSRP Jakarta dari situs resmi, dalam rupiah. Tulis dalam "Rp xx juta". Sebut selisih antar varian bila ada lebih dari satu varian.
6. Nada informatif dan netral. Hindari kata promosi seperti "terbaik", "sempurna", atau "wajib punya".
7. Bagian "Cocok untuk siapa" hanya berisi kesimpulan yang wajar dari data (kategori, kapasitas mesin, harga), dengan bahasa yang berhati-hati.
8. Bagian "ulasan_media": sebut pendapat sebagai "menurut ulasan {{nama media}}", dengan kata-katamu sendiri dan tanpa mengutip kalimat asli. Jika ada perbedaan pendapat antar media, sebutkan keduanya.
9. Lewati bagian yang datanya sama sekali kosong (jangan memaksa menulis), tetapi tulis minimal 4 bagian.
10. Satu bagian terdiri dari 2 sampai 4 kalimat. Tanpa markdown.

Balas HANYA dengan JSON seperti ini:
{{"sections": [{{"h": "Sekilas", "p": "..."}}, {{"h": "Mesin dan performa", "p": "..."}}, {{"h": "Fitur dan dimensi", "p": "..."}}, {{"h": "Kata media otomotif", "p": "..."}}, {{"h": "Harga dan varian", "p": "..."}}, {{"h": "Cocok untuk siapa", "p": "..."}}, {{"h": "Yang perlu diperhatikan", "p": "..."}}]}}"""


def core_of(m, research):
    core = {k: m.get(k) for k in FIELDS}
    r = research.get(m["id"])
    if r:   # ringkas: spesifikasi tanpa URL, ulasan dengan nama media
        core["riset"] = {"spesifikasi": {k: v["value"] for k, v in r.get("specs", {}).items()},
                         "ulasan_media": [{"media": u["media"], "kelebihan": u["kelebihan"], "kekurangan": u["kekurangan"]} for u in r.get("ulasan", [])]}
    return core


def digest(m, research):
    core = core_of(m, research)
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def write(client, m, research):
    core = core_of(m, research)
    r = client.messages.create(
        model=MODEL, max_tokens=4000,
        messages=[{"role": "user", "content": PROMPT.format(data=json.dumps(core, ensure_ascii=False, indent=1))}],
    )
    text = "".join(b.text for b in r.content if b.type == "text")
    found = re.search(r"\{.*\}", text, re.S)
    data = json.loads(found.group(0))
    secs = [{"h": str(s["h"]), "p": str(s["p"])} for s in data["sections"] if s.get("h") and s.get("p")]
    if len(secs) < 3:
        raise ValueError("artikel terlalu pendek")
    return secs


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY belum diatur")
    client = anthropic.Anthropic()
    motors = json.loads(SRC.read_text(encoding="utf-8"))
    arts = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    research = json.loads(RESEARCH.read_text(encoding="utf-8")) if RESEARCH.exists() else {}
    ids = {m["id"] for m in motors}
    arts = {k: v for k, v in arts.items() if k in ids}          # buang artikel motor yang sudah tidak dijual
    todo = [m for m in motors if arts.get(m["id"], {}).get("hash") != digest(m, research)]
    print(f"{len(todo)} artikel perlu ditulis (dari {len(motors)} motor)")
    for i, m in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {m['brand']} {m['name']}")
        try:
            arts[m["id"]] = {"hash": digest(m, research), "sections": write(client, m, research)}
        except Exception as e:
            print("   GAGAL:", e)
            continue
        OUT.write_text(json.dumps(arts, ensure_ascii=False, indent=1), encoding="utf-8")
        time.sleep(1)
    OUT.write_text(json.dumps(arts, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Selesai: {len(arts)} artikel di {OUT}")


if __name__ == "__main__":
    main()
