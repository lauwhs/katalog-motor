"""
Pengumpul data motor Kawasaki Indonesia -> data/kawasaki.json

Cara kerja:
1. Buka halaman utama resmi, kumpulkan link motor roda dua (satu entri per model).
   Motor yang tidak ada lagi di situs resmi otomatis hilang dari hasil (= tidak dijual lagi).
2. Ambil teks tiap halaman model. Kalau teksnya tidak berubah sejak kemarin, pakai hasil lama.
3. Kalau berubah, minta Claude mengekstrak spesifikasi menjadi JSON (tanpa menebak).
4. Gabungkan dengan data/overrides.json (isian manual: tanggal rilis, top speed, dll).

Jalankan:  ANTHROPIC_API_KEY=... python scrape_kawasaki.py
"""
import hashlib, json, os, re, sys, time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import anthropic

# ===== PENGATURAN: cek dan sesuaikan dengan struktur situs resmi =====
BASE = "https://www.kawasaki-motor.co.id"
LISTING_URLS = [BASE + "/id-id/"]              # halaman utama memuat menu semua motor
SECTION = "/id-id/sepeda-motor/"               # hanya roda dua (Mule, Teryx, Jet Ski, ATV dibuang)
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
MIN_MODELS = 20      # kalau model yang ditemukan lebih sedikit, anggap situs error dan jangan timpa data
DELAY = 2            # jeda antar permintaan (detik), sopan terhadap server
# =====================================================================

BODY_MAP = {
    "sport": "Sport", "supersport": "Sport", "hypersport": "Sport",
    "supernaked": "Naked", "mini-naked": "Naked", "retro-sport": "Retro", "retro-classic": "Retro",
    "street-cruiser": "Cruiser", "sport-cruiser": "Cruiser",
    "adventure-touring": "Adventure", "rally-adventure": "Adventure",
    "dual-purpose-150": "Trail", "dual-purpose-230-250": "Trail", "off-road": "Trail",
    "supermoto": "Supermoto",
    "full-size-motocross": "Motocross", "full-size-cross-country": "Motocross", "kx-youth": "Motocross",
}
BODY_BY_MODEL = {"ninja-e-1": "Sport", "z-e-1": "Naked"}   # kategori "ev" tidak informatif

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "kawasaki.json"
CACHE = ROOT / "data" / ".cache_kawasaki.json"
OVERRIDES = ROOT / "data" / "overrides.json"
HEADERS = {"User-Agent": "KatalogMotorBot/1.0 (+kontak: isi-email-kamu)"}

PROMPT = """Ekstrak spesifikasi motor dari teks halaman resmi Kawasaki Indonesia di bawah.
Balas HANYA dengan satu objek JSON, tanpa teks lain, dengan kunci:
name (nama model tanpa kata Kawasaki), body (salah satu: Matic, Bebek, Sport, Naked, Trail, Cruiser, Touring, Lainnya),
engine (Bensin atau Listrik), cc (angka, 0 untuk listrik), price (harga termurah semua varian dalam rupiah, angka),
variants (daftar {name, price}; sertakan tahun model di awal nama varian bila tertulis), speed (km/jam), kmpl (km per liter), acc (detik 0-100 km/jam), range_km (listrik), summary (1-2 kalimat Indonesia).
Aturan: isi null untuk data yang TIDAK tertulis di teks. Jangan menebak, menghitung, atau memakai pengetahuan di luar teks.
Jika halaman ini bukan halaman motor yang dijual, balas {"skip": true}.
Petunjuk dari alamat situs (boleh dipakai untuk body): {hint}

TEKS HALAMAN (beberapa halaman digabung):
"""


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    time.sleep(DELAY)
    return r.text


def find_models():
    """Kelompokkan link menjadi satu entri per model (halaman varian per tahun digabung)."""
    groups = {}
    for u in LISTING_URLS:
        soup = BeautifulSoup(get(u), "html.parser")
        for a in soup.find_all("a", href=True):
            url = urljoin(BASE, a["href"].split("#")[0].split("?")[0]).rstrip("/")
            path = urlparse(url).path
            if not path.startswith(SECTION):
                continue
            parts = path.split("/")   # ['', 'id-id', 'sepeda-motor', seri, kategori, model, (varian)]
            if len(parts) < 6:
                continue
            g = groups.setdefault("/".join(parts[:6]), set())
            if len(parts) > 6:
                g.add(url)
    return groups


def latest_variants(urls):
    def yr(u):
        m = re.match(r"(\d{4})", u.rsplit("/", 1)[-1])
        return int(m.group(1)) if m else 0
    if not urls:
        return [], None
    top = max(yr(u) for u in urls)
    return sorted(u for u in urls if yr(u) == top)[:4], (top or None)


def page_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "footer", "noscript"]):
        t.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()[:15000]


def extract(client, text, hint):
    msg = client.messages.create(
        model=MODEL, max_tokens=4000,
        messages=[{"role": "user", "content": PROMPT.replace("{hint}", hint) + text}],
    )
    raw = "".join(b.text for b in msg.content if b.type == "text").strip()   # lewati blok "thinking"
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    return json.loads(raw)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY belum diatur")
    client = anthropic.Anthropic()
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8")) if OVERRIDES.exists() else {}

    groups = find_models()
    print(f"Ditemukan {len(groups)} model motor")
    if len(groups) < MIN_MODELS:
        sys.exit(f"Hanya {len(groups)} model (minimal {MIN_MODELS}). Struktur situs mungkin berubah, data lama dibiarkan.")

    result, new_cache = [], {}
    for model_path, variant_urls in sorted(groups.items()):
        model_url = BASE + model_path
        parts = model_path.split("/")
        hint = f"seri={parts[3]}, kategori={parts[4]}, model={parts[5]}"
        variants, year = latest_variants(variant_urls)
        key = model_url
        try:
            texts = []
            for u in [model_url] + variants:
                try:
                    texts.append(page_text(get(u))[:7000])
                except Exception as e:
                    print("  lewati halaman", u, e)
            if not texts:
                raise RuntimeError("semua halaman gagal dibuka")
            text = "\n=== HALAMAN ===\n".join(texts)
            h = hashlib.sha256(text.encode()).hexdigest()
            old = cache.get(key)
            if old and old["hash"] == h:
                data = old["data"]
            else:
                print("Ekstrak:", model_url)
                data = extract(client, text, hint)
            new_cache[key] = {"hash": h, "data": data}
        except Exception as e:
            print("GAGAL", model_url, e)
            if key in cache:           # pakai data lama kalau gagal sementara
                new_cache[key] = cache[key]
                data = cache[key]["data"]
            else:
                continue
        if data.get("skip") or not data.get("name"):
            continue
        if not data.get("price"):
            print("  dilewati (harga tidak ditemukan):", data["name"])
            continue
        # nama tanpa awalan tahun, varian hanya dari tahun model terbaru
        data["name"] = re.sub(r"^\d{4}\s+", "", data["name"]).strip()
        vs = []
        for v in data.get("variants") or []:
            m = re.match(r"(\d{4})\s+(.*)", v.get("name") or "")
            vs.append({"year": int(m.group(1)) if m else None, "name": (m.group(2) if m else v.get("name")), "price": v.get("price")})
        # satu entri per nama varian, ambil dari tahun terbaru masing-masing
        best = {}
        for v in vs:
            if not v["price"]:
                continue
            k = re.sub(r"\s+", " ", (v["name"] or "").upper()).strip()
            if k not in best or (v["year"] or 0) > (best[k]["year"] or 0):
                best[k] = v
        vs = [{"name": v["name"], "price": v["price"]} for v in best.values()]
        if vs:
            data["price"] = min(v["price"] for v in vs)
        data["variants"] = vs
        data["body"] = BODY_BY_MODEL.get(parts[5]) or BODY_MAP.get(parts[4]) or data.get("body")
        item = {
            "id": "kawasaki-" + slug(re.sub(r"^\d{4}-", "", parts[5])),
            "brand": "Kawasaki",
            "name": data["name"],
            "body": data.get("body") or "Lainnya",
            "engine": data.get("engine") or "Bensin",
            "cc": data.get("cc"),
            "price": data["price"],
            "variants": data.get("variants") or [],
            "model_year": year,
            "release": None, "speed": data.get("speed"), "kmpl": data.get("kmpl"),
            "acc": data.get("acc"), "range": data.get("range_km"),
            "sum": data.get("summary") or "",
            "source": model_url, "checked": date.today().isoformat(),
        }
        item.update({k: v for k, v in overrides.get(item["id"], {}).items() if v is not None})
        result.append(item)

    if len(result) < MIN_MODELS // 2:
        sys.exit(f"Hanya {len(result)} motor berhasil diekstrak. Data lama tidak ditimpa. Cek pesan GAGAL di atas.")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(sorted(result, key=lambda x: x["name"]), ensure_ascii=False, indent=1), encoding="utf-8")
    CACHE.write_text(json.dumps(new_cache, ensure_ascii=False), encoding="utf-8")
    print(f"Selesai: {len(result)} motor Kawasaki tersimpan di {OUT}")


if __name__ == "__main__":
    main()
