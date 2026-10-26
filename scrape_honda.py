"""
Pengumpul data motor Honda (Astra Honda Motor) -> data/honda.json

Situs astra-honda.com memuat daftar motornya lewat API resmi miliknya sendiri, jadi skrip ini membaca API itu
(tanpa browser otomatis):
  /api/v1/product/category        daftar kategori (Matic, Cub, Sport, ...)
  /api/v1/product?category=<id>   daftar motor per kategori (harga OTR, foto produk, fitur + foto fitur)
  /api/v1/product/<slug>          detail satu motor
Motor yang tidak ada lagi di daftar resmi otomatis hilang (= tidak dijual lagi).

Jalankan:  ANTHROPIC_API_KEY=... python scrape_honda.py
"""
import hashlib, json, os, re, sys, time
from datetime import date
from pathlib import Path

import requests
import anthropic

BASE = "https://www.astra-honda.com"
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
MIN_MODELS = 15
DELAY = 1.5
ROOT = Path(__file__).parent
OUT = ROOT / "data" / "honda.json"
CACHE = ROOT / "data" / ".cache_honda.json"
OVERRIDES = ROOT / "data" / "overrides.json"
HEADERS = {"User-Agent": "KatalogMotorBot/1.0 (+kontak: isi-email-kamu)", "Accept": "application/json",
           "X-Requested-With": "XMLHttpRequest", "Referer": BASE + "/product"}
BODY_DEFAULT = {"matic": "Matic", "cub": "Bebek", "sport": "Sport"}
GTAGS = [(r"meter|speedo|dash|panel|instrument|cluster|tft", "Panel instrumen"), (r"head ?light|head ?lamp|tail|lamp|light|led|winker", "Lampu"),
         (r"suspen|fork|shock|swingarm", "Suspensi"), (r"wheel|tire|tyre|ban |velg|rim|brake|disc|rem|abs", "Roda dan rem"),
         (r"exhaust|muffler|knalpot|silencer", "Knalpot"), (r"engine|mesin|cylinder|radiator|esp", "Mesin"),
         (r"seat|jok|saddle|bagasi|u-?box|luggage", "Jok dan bagasi")]
DROP = {"tags", "azure", "imagekit", "original", "extension", "thumbnail", "thumbnail_mobile", "image", "images", "position",
        "path", "colors", "article_ids", "product_ids", "tire_ids", "product_draft_ids", "articleDraft_ids", "category", "id", "updated_at", "created_at"}

PROMPT = """Ekstrak data motor dari JSON resmi Astra Honda Motor di bawah. Balas HANYA dengan satu objek JSON, tanpa teks lain, berkunci:
name (nama model, contoh "BeAT", "Vario 160"), body (pilih satu: Matic, Bebek, Sport, Naked, Trail, Retro, Adventure, Cruiser, Touring, Supermoto, Motocross, Lainnya),
engine (Bensin atau Listrik), cc (angka, 0 untuk listrik), price (harga termurah semua varian, rupiah, angka), variants (daftar {{name, price}}),
speed (km/jam), kmpl (km per liter), acc (detik 0-100 km/jam), range_km (listrik), summary (1-2 kalimat Indonesia),
specs (objek dengan kunci tenaga, torsi, bobot, tinggi_jok, tangki, transmisi, rem, suspensi, ban, fitur; masing-masing teks singkat atau null).
Aturan: isi null untuk data yang TIDAK tertulis di JSON. Jangan menebak, menghitung, atau memakai pengetahuan di luar JSON.
Petunjuk: kategori resmi = {kategori}; harga di daftar resmi = {harga} rupiah (dipakai bila JSON tidak memuat harga per varian).

JSON:
"""


def get_json(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    time.sleep(DELAY)
    return r.json()


def as_list(js):
    if isinstance(js, list):
        return js
    if isinstance(js, dict):
        for k in ("data", "products", "items", "results"):
            if isinstance(js.get(k), list):
                return js[k]
    return []


def clean(o):
    """Buang bagian JSON yang besar dan tidak perlu (tag SEO, alamat gambar) sebelum dikirim ke Claude."""
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items() if k not in DROP}
    if isinstance(o, list):
        return [clean(x) for x in o]
    if isinstance(o, str) and re.match(r"https?://(imagewebsite|asset|ik\.imagekit)", o):
        return None
    return o


def gallery_of(p):
    out, seen = [], set()
    for d in (p.get("features") or {}).get("dataset", []):
        for im in d.get("image", []):
            url = im.get("url") or im.get("azure") or ""
            if not url or url in seen or re.search(r"\.svg", url):
                continue
            seen.add(url)
            title = (d.get("title") or "").strip()
            tag = next((lab for rx, lab in GTAGS if re.search(rx, title.lower() + " ", re.I)), "Foto")
            out.append({"url": url, "caption": title[:90], "tag": tag})
    return out[:12]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY belum diatur")
    client = anthropic.Anthropic()
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8")) if OVERRIDES.exists() else {}

    cats = as_list(get_json(BASE + "/api/v1/product/category"))
    print("Kategori:", ", ".join(c.get("title", "?") for c in cats))
    products = {}
    for c in cats:
        for p in as_list(get_json(f"{BASE}/api/v1/product?category={c['id']}")):
            if p.get("redirect_link"):           # nama lama yang dialihkan ke model lain
                continue
            products.setdefault(p["slug"], (c, p))
    print(f"Ditemukan {len(products)} model motor Honda")
    if len(products) < MIN_MODELS:
        sys.exit(f"Hanya {len(products)} model (minimal {MIN_MODELS}). API mungkin berubah, data lama dibiarkan.")

    result, new_cache, first = [], {}, True
    for sl, (cat, p) in sorted(products.items()):
        price_list = (p.get("prices") or {}).get("value") or 0
        api_url = (p.get("url") or {}).get("api") or f"{BASE}/api/v1/product/{sl}"
        try:
            detail = get_json(api_url)
            if first:
                print("Kunci JSON detail:", list(detail.keys()) if isinstance(detail, dict) else type(detail))
                first = False
            text = json.dumps(clean({"daftar": {k: v for k, v in p.items() if k != "features"}, "detail": detail}),
                              ensure_ascii=False, separators=(",", ":"))[:22000]
            h = hashlib.sha256(text.encode()).hexdigest()
            old = cache.get(sl)
            if old and old["hash"] == h:
                data = old["data"]
            else:
                print("Ekstrak:", p["title"])
                r = client.messages.create(model=MODEL, max_tokens=4000, messages=[{"role": "user", "content":
                    PROMPT.format(kategori=cat.get("title"), harga=price_list) + text}])
                raw = "".join(b.text for b in r.content if b.type == "text").strip()
                data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
            new_cache[sl] = {"hash": h, "data": data}
        except Exception as e:
            print("GAGAL", sl, e)
            if sl in cache:
                new_cache[sl] = cache[sl]
                data = cache[sl]["data"]
            else:
                continue
        price = data.get("price") or price_list
        if not price:
            print("  dilewati (harga tidak ditemukan):", p["title"])
            continue
        years = [int(y) for y in (p.get("years") or []) if str(y).isdigit()]
        variants = [{"name": v.get("name"), "price": v.get("price")} for v in (data.get("variants") or []) if v.get("name") and v.get("price")]
        specs = {k: str(v) for k, v in (data.get("specs") or {}).items() if v and k in
                 ("tenaga", "torsi", "bobot", "tinggi_jok", "tangki", "transmisi", "rem", "suspensi", "ban", "fitur")}
        item = {
            "id": "honda-" + slug(sl), "brand": "Honda", "name": data.get("name") or p["title"],
            "body": data.get("body") or BODY_DEFAULT.get((cat.get("slug") or "").lower(), "Lainnya"),
            "engine": data.get("engine") or "Bensin", "cc": data.get("cc"), "price": price, "price_type": "OTR Jakarta",
            "variants": variants, "model_year": max(years) if years else None, "release": None,
            "speed": data.get("speed"), "kmpl": data.get("kmpl"), "acc": data.get("acc"), "range": data.get("range_km"),
            "sum": data.get("summary") or "", "specs": specs,
            "image": p.get("thumbnail"), "image_credit": "Astra Honda Motor", "gallery": gallery_of(p), "trusted_images": True,
            "source": f"{BASE}/product/{sl}", "checked": date.today().isoformat(),
        }
        item.update({k: v for k, v in overrides.get(item["id"], {}).items() if v is not None})
        result.append(item)

    if len(result) < MIN_MODELS // 2:
        sys.exit(f"Hanya {len(result)} motor berhasil diekstrak. Data lama tidak ditimpa. Cek pesan GAGAL di atas.")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(sorted(result, key=lambda x: x["name"]), ensure_ascii=False, indent=1), encoding="utf-8")
    CACHE.write_text(json.dumps(new_cache, ensure_ascii=False), encoding="utf-8")
    print(f"Selesai: {len(result)} motor Honda tersimpan di {OUT}; foto utama: {sum(1 for x in result if x['image'])}, galeri: {sum(len(x['gallery']) for x in result)} foto")


if __name__ == "__main__":
    main()
