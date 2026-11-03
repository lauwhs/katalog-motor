"""
Pengumpul data motor Yamaha Indonesia -> data/yamaha.json

Situs yamaha-motor.co.id dibuat di server, jadi cukup dibaca dengan requests + BeautifulSoup:
  /products/category/<kategori>/   daftar motor tiap kategori (MAXI, Classy, Matic, Sport, Off-Road, Moped)
  /product/<slug>/                 halaman satu motor (varian dan harga, fitur + foto fitur, tabel spesifikasi)
Motor yang tidak ada lagi di daftar resmi otomatis hilang (= tidak dijual lagi).
Jalankan:  ANTHROPIC_API_KEY=... python scrape_yamaha.py
"""
import hashlib, json, os, re, sys, time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import anthropic

BASE = "https://www.yamaha-motor.co.id"
CATEGORIES = ["maxi-yamaha", "classy", "matic", "sport", "off-road", "moped"]
BODY_DEFAULT = {"maxi-yamaha": "Matic", "classy": "Matic", "matic": "Matic", "sport": "Sport", "off-road": "Trail", "moped": "Bebek"}
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
MIN_MODELS = 15
DELAY = 1.5
ROOT = Path(__file__).parent
OUT = ROOT / "data" / "yamaha.json"
CACHE = ROOT / "data" / ".cache_yamaha.json"
OVERRIDES = ROOT / "data" / "overrides.json"
HEADERS = {"User-Agent": "KatalogMotorBot/1.0 (+kontak: isi-email-kamu)"}
PRODUCT = re.compile(r"^/product/([^/]+)/?$")
GTAGS = [(r"meter|speedo|dash|panel|instrument|cluster|tft|display|cockpit", "Panel instrumen"), (r"head ?light|head ?lamp|tail|lamp|light|led|winker", "Lampu"),
         (r"suspen|fork|shock|swingarm|damper", "Suspensi"), (r"wheel|tire|tyre|ban |velg|rim|brake|disc|abs", "Roda dan rem"),
         (r"exhaust|muffler|knalpot|silencer", "Knalpot"), (r"engine|mesin|cylinder|radiator|cvt|vva", "Mesin"),
         (r"seat|jok|saddle|luggage|bagasi|u-?box", "Jok dan bagasi")]

PROMPT = """Ekstrak data motor dari teks halaman resmi Yamaha Indonesia di bawah. Balas HANYA dengan satu objek JSON, tanpa teks lain, berkunci:
name (nama model tanpa kata Yamaha; bedakan model yang namanya mirip memakai judul halaman, mis. "NMAX Turbo" dan "NMAX 155 Connected"),
body (pilih satu: Matic, Bebek, Sport, Naked, Trail, Retro, Adventure, Supermoto, Motocross, Lainnya),
engine (Bensin atau Listrik), cc (angka, 0 untuk listrik), price (harga varian termurah, rupiah, angka; "Rp 46.945.000" = 46945000),
variants (daftar {{name, price}} semua varian yang punya harga), speed (km/jam), kmpl (km per liter), acc (detik 0-100 km/jam), range_km (listrik), summary (1-2 kalimat Indonesia),
specs (objek berkunci tenaga, torsi, bobot, tinggi_jok, tangki, transmisi, rem, suspensi, ban, fitur; masing-masing teks singkat dengan satuannya atau null;
 tenaga = daya maksimum, bobot = berat isi, fitur = fitur utama dipisah koma).
Aturan: isi null untuk data yang TIDAK tertulis di teks. Jangan menebak, menghitung, atau memakai pengetahuan di luar teks.
Petunjuk: kategori resmi = {kategori}; alamat = {slug}; judul halaman = {judul}.

TEKS HALAMAN:
"""


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    time.sleep(DELAY)
    return r.text


def list_products():
    found = {}
    for cat in CATEGORIES:
        soup = BeautifulSoup(get(f"{BASE}/products/category/{cat}/"), "html.parser")
        for a in soup.find_all("a", href=True):
            m = PRODUCT.match(urlparse(urljoin(BASE, a["href"].split("#")[0].split("?")[0])).path)
            if m:
                found.setdefault(m.group(1), cat)
    return found


def page_text(soup):
    s = BeautifulSoup(str(soup), "html.parser")
    for t in s(["script", "style", "nav", "footer", "noscript"]):
        t.decompose()
    text = re.sub(r"[ \t]+", " ", s.get_text("\n"))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    cut = text.find("360 IMAGE")                     # setelah tabel spesifikasi hanya video dan aplikasi
    return (text[:cut] if cut > 0 else text)[:20000]


def images_of(soup, page_url):
    """Foto utama = og:image; galeri = foto fitur (dengan judul fitur) + foto pilihan warna."""
    og = soup.find("meta", attrs={"property": "og:image"})
    image = urljoin(page_url, og["content"]) if og and og.get("content") else None
    isov = lambda i: "/uploads/products/overview/" in (i.get("data-src") or i.get("src") or "")
    gallery, seen = [], set()
    for img in soup.find_all("img"):
        if not isov(img):
            continue
        url = urljoin(page_url, (img.get("data-src") or img.get("src")).strip())
        if url in seen:
            continue
        seen.add(url)
        node = img
        while node.parent is not None and node.parent.name not in ("body", "html"):
            if sum(1 for x in node.parent.find_all("img") if isov(x)) > 1:
                break
            node = node.parent
        txt = node.get_text("\n", strip=True)
        cap = txt.split("\n")[0][:90] if txt and len(txt) < 800 else (img.get("alt") or "")
        tag = next((lab for rx, lab in GTAGS if re.search(rx, cap + " ", re.I)), "Foto")
        gallery.append({"url": url, "caption": cap, "tag": tag})
    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("src") or ""
        url = urljoin(page_url, src.strip())
        if "/uploads/products/new_product_model_image/" in src and url not in seen and sum(1 for g in gallery if g["caption"] == "Pilihan warna") < 4:
            seen.add(url)
            gallery.append({"url": url, "caption": "Pilihan warna", "tag": "Foto"})
    if not image:
        image = next((g["url"] for g in gallery if g["caption"] == "Pilihan warna"), None)
    return image, gallery[:16]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY belum diatur")
    client = anthropic.Anthropic()
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8")) if OVERRIDES.exists() else {}

    products = list_products()
    print(f"Ditemukan {len(products)} model motor Yamaha")
    if len(products) < MIN_MODELS:
        sys.exit(f"Hanya {len(products)} model (minimal {MIN_MODELS}). Struktur situs mungkin berubah, data lama dibiarkan.")

    result, new_cache = [], {}
    for sl, cat in sorted(products.items()):
        url = f"{BASE}/product/{sl}/"
        try:
            html = get(url)
            soup = BeautifulSoup(html, "html.parser")
            text = page_text(soup)
            title = (soup.title.get_text(strip=True) if soup.title else "")
            h = hashlib.sha256(text.encode()).hexdigest()
            old = cache.get(sl)
            if old and old["hash"] == h:
                data = old["data"]
            else:
                print("Ekstrak:", sl)
                r = client.messages.create(model=MODEL, max_tokens=4000, messages=[{"role": "user", "content":
                    PROMPT.format(kategori=cat, slug=sl, judul=title) + text}])
                raw = "".join(b.text for b in r.content if b.type == "text").strip()
                data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
            new_cache[sl] = {"hash": h, "data": data}
            image, gallery = images_of(soup, url)
        except Exception as e:
            print("GAGAL", sl, e)
            if sl in cache:
                new_cache[sl] = cache[sl]
                data, image, gallery = cache[sl]["data"], None, []
            else:
                continue
        if not data.get("name") or not data.get("price"):
            print("  dilewati (nama/harga tidak ditemukan):", sl)
            continue
        variants = [{"name": v.get("name"), "price": v.get("price")} for v in (data.get("variants") or []) if v.get("name") and v.get("price")]
        specs = {k: str(v) for k, v in (data.get("specs") or {}).items() if v and k in
                 ("tenaga", "torsi", "bobot", "tinggi_jok", "tangki", "transmisi", "rem", "suspensi", "ban", "fitur")}
        item = {
            "id": "yamaha-" + slug(sl), "brand": "Yamaha", "name": data["name"],
            "body": data.get("body") or BODY_DEFAULT.get(cat, "Lainnya"), "engine": data.get("engine") or "Bensin",
            "cc": data.get("cc"), "price": data["price"], "price_type": "OTR Jakarta (harga rekomendasi)", "variants": variants,
            "model_year": None, "release": None, "speed": data.get("speed"), "kmpl": data.get("kmpl"), "acc": data.get("acc"),
            "range": data.get("range_km"), "sum": data.get("summary") or "", "specs": specs,
            "image": image, "image_credit": "Yamaha Indonesia Motor Manufacturing", "gallery": gallery, "trusted_images": True,
            "source": url, "checked": date.today().isoformat(),
        }
        item.update({k: v for k, v in overrides.get(item["id"], {}).items() if v is not None})
        result.append(item)

    from collections import Counter
    pakai = Counter(g["url"] for x in result for g in x["gallery"])
    for x in result:                         # foto yang sama di banyak motor (mis. banner garansi) dibuang dari galeri
        x["gallery"] = [g for g in x["gallery"] if pakai[g["url"]] <= 3]
    if len(result) < MIN_MODELS // 2:
        sys.exit(f"Hanya {len(result)} motor berhasil diekstrak. Data lama tidak ditimpa. Cek pesan GAGAL di atas.")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(sorted(result, key=lambda x: x["name"]), ensure_ascii=False, indent=1), encoding="utf-8")
    CACHE.write_text(json.dumps(new_cache, ensure_ascii=False), encoding="utf-8")
    print(f"Selesai: {len(result)} motor Yamaha tersimpan; foto utama: {sum(1 for x in result if x['image'])}, galeri: {sum(len(x['gallery']) for x in result)} foto")


if __name__ == "__main__":
    main()
