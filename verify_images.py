"""
Periksa foto dengan Claude (membaca isi gambar) lalu buang yang salah: motor lain, ATV, UTV, logo, banner.
Foto yang lolos diberi label bagian (Panel instrumen, Lampu, Suspensi, dst.). Hasil per foto disimpan di
data/.cache_images.json, jadi tiap foto hanya diperiksa sekali.

Jalankan SETELAH scrape dan SEBELUM merge:  python verify_images.py
"""
import base64, json, os, re, sys, time
from pathlib import Path
import requests
import anthropic

MODEL = os.getenv("VISION_MODEL", "claude-haiku-5-5")
DATA = Path(__file__).parent / "data"
CACHE = DATA / ".cache_images.json"
SKIP = {"motor.json", "overrides.json", "enriched.json", "research.json", "articles.json", "commons.json"}
HEADERS = {"User-Agent": "Mozilla/5.0 (KatalogMotorBot/1.0)"}
LABELS = {"Foto", "Panel instrumen", "Lampu", "Suspensi", "Roda dan rem", "Knalpot", "Mesin", "Jok", "Lainnya"}

PROMPT = """Foto ini ditemukan di halaman produk motor {brand} {name} (kategori {body}, {cc} cc). Tentukan isinya.
Balas HANYA JSON: {{"jenis": "sama" | "bagian" | "lain" | "bukan_motor", "label": "Foto" | "Panel instrumen" | "Lampu" | "Suspensi" | "Roda dan rem" | "Knalpot" | "Mesin" | "Jok" | "Lainnya"}}
- sama: foto motor utuh yang tampak sesuai {name} (bentuk dan kategori {body}).
- bagian: foto close-up bagian motor (meter, lampu, suspensi, roda, knalpot, mesin, jok) yang tidak bertentangan dengan model ini.
- lain: tampak kendaraan yang jelas berbeda: MOBIL (termasuk mobil yang kebetulan bernama sama dengan motor ini), motor dengan jenis atau model berbeda (misalnya motor sport padahal model ini matic), ATV, UTV, side-by-side, jet ski, atau produk lain.
- bukan_motor: logo, ikon, banner teks, peta, orang tanpa motor, dan sejenisnya.
Untuk "sama", isi label "Foto"."""


def fetch(url):
    r = requests.get(url, headers={**HEADERS, "Referer": url.split("/")[0] + "//" + url.split("/")[2] + "/"}, timeout=30)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype not in ("image/jpeg", "image/png", "image/webp", "image/gif") or len(r.content) > 4_500_000:
        raise ValueError(f"tidak bisa dipakai ({ctype}, {len(r.content)} byte)")
    return ctype, base64.b64encode(r.content).decode()


def classify(client, m, url):
    ctype, b64 = fetch(url)
    r = client.messages.create(model=MODEL, max_tokens=1500, messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": ctype, "data": b64}},
        {"type": "text", "text": PROMPT.format(**{k: m.get(k) for k in ("brand", "name", "body", "cc")})}]}])
    text = "".join(b.text for b in r.content if b.type == "text")
    found = re.search(r"\{.*?\}", text, re.S)
    v = json.loads(found.group(0))
    label = v.get("label") if v.get("label") in LABELS else "Lainnya"
    return {"jenis": v.get("jenis"), "label": "Foto" if v.get("jenis") == "sama" else label}


def apply(m, verdict):
    """Pakai hasil pemeriksaan: gambar utama = foto motor utuh pertama yang lolos; galeri = sisanya yang lolos."""
    cands = ([{"url": m["image"], "caption": "", "tag": "Foto"}] if m.get("image") else []) + m.get("gallery", [])
    keep = []
    for g in cands:
        v = verdict.get(g["url"])
        if v and v["jenis"] in ("sama", "bagian"):
            keep.append({**g, "tag": v["label"], "jenis": v["jenis"]})
    utama = next((g for g in keep if g["jenis"] == "sama"), None)
    m["image"] = utama["url"] if utama else None
    m["gallery"] = [{"url": g["url"], "caption": g["caption"], "tag": g["tag"]} for g in keep if g is not utama][:12]


def filter_commons(motors, commons, cache, classify_fn):
    """Periksa foto Wikimedia Commons: buang yang bukan motor ini (mobil, motor lain, dsb.)."""
    by_id = {m["id"]: m for m in motors}
    for mid, photos in commons.items():
        m = by_id.get(mid)
        if not m:
            continue
        keep = []
        for g in photos:
            if g["url"] not in cache:
                try:
                    cache[g["url"]] = classify_fn(m, g["url"])
                    print(f"{m['name']} (Commons): {cache[g['url']]['jenis']}  {g['url'][-45:]}")
                except Exception as e:
                    print(f"{m['name']} (Commons): gagal periksa {g['url'][-45:]} ({e})")
                    continue
                time.sleep(0.3)
            r = cache[g["url"]]
            if r["jenis"] in ("sama", "bagian"):
                keep.append({**g, "tag": r["label"] if r["label"] not in ("Foto", "Lainnya") else g.get("tag", "Foto")})
        commons[mid] = keep
    return commons


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY belum diatur")
    client = anthropic.Anthropic()
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    if "--commons" in sys.argv:                      # hanya periksa foto Wikimedia Commons
        motors = []
        for f in sorted(DATA.glob("*.json")):
            if f.name not in SKIP and not f.name.startswith("."):
                motors += json.loads(f.read_text(encoding="utf-8"))
        cf = DATA / "commons.json"
        if cf.exists():
            com = filter_commons(motors, json.loads(cf.read_text(encoding="utf-8")), cache, lambda m, u: classify(client, m, u))
            cf.write_text(json.dumps(com, ensure_ascii=False, indent=1), encoding="utf-8")
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print("Foto Commons setelah diperiksa:", sum(len(x) for x in com.values()), "foto untuk", sum(1 for x in com.values() if x), "motor")
        return
    for f in sorted(DATA.glob("*.json")):
        if f.name in SKIP or f.name.startswith("."):
            continue
        motors = json.loads(f.read_text(encoding="utf-8"))
        for m in motors:
            if m.get("trusted_images"):          # foto resmi yang sudah berlabel (mis. galeri fitur Honda)
                continue
            urls = ([m["image"]] if m.get("image") else []) + [g["url"] for g in m.get("gallery", [])]
            for u in urls:
                if u in cache:
                    continue
                try:
                    cache[u] = classify(client, m, u)
                    print(f"{m['name']}: {cache[u]['jenis']} / {cache[u]['label']}  {u[-50:]}")
                except Exception as e:
                    print(f"{m['name']}: gagal periksa {u[-50:]} ({e})")
                CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
                time.sleep(0.3)
            apply(m, cache)
            print(f"== {m['name']}: gambar utama {'ada' if m['image'] else 'TIDAK ADA'}, galeri {len(m['gallery'])} foto")
        f.write_text(json.dumps(motors, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
