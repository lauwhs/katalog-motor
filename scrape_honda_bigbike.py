"""
Pengumpul data Honda Big Bike -> data/honda_bigbike.json

Big Bike punya situs sendiri (astra-honda.com/hondabigbike) yang halamannya dibuat di server, jadi cukup dibaca
dengan requests + BeautifulSoup (tanpa browser otomatis). Cara kerjanya sama seperti scrape_kawasaki.py:
daftar model diambil dari halaman utama, tiap halaman model dibaca Claude menjadi JSON, foto diambil dari halaman.
Jalankan:  ANTHROPIC_API_KEY=... python scrape_honda_bigbike.py
"""
import hashlib, json, os, re, sys, time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import anthropic

BASE = "https://www.astra-honda.com"
HOME = BASE + "/hondabigbike"
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
MIN_MODELS = 5
DELAY = 2
ROOT = Path(__file__).parent
OUT = ROOT / "data" / "honda_bigbike.json"
CACHE = ROOT / "data" / ".cache_honda_bigbike.json"
OVERRIDES = ROOT / "data" / "overrides.json"
HEADERS = {"User-Agent": "KatalogMotorBot/1.0 (+kontak: isi-email-kamu)"}
LINK = re.compile(r"^/hondabigbike/product/(naked|sport|adventure|cruiser)/[^/]+$")
BODY_DEFAULT = {"naked": "Naked", "sport": "Sport", "adventure": "Adventure", "cruiser": "Cruiser"}
GTAGS = [(r"meter|speedo|dash|panel|instrument|cluster|tft", "Panel instrumen"), (r"head ?light|head ?lamp|tail|lamp|light|led|winker", "Lampu"),
         (r"suspen|fork|shock|swingarm|showa", "Suspensi"), (r"wheel|tire|tyre|velg|rim|brake|disc|abs", "Roda dan rem"),
         (r"exhaust|muffler|knalpot|silencer", "Knalpot"), (r"engine|mesin|cylinder|radiator|cc |dohc|clutch", "Mesin"),
         (r"seat|jok|saddle|luggage|pannier", "Jok dan bagasi")]

PROMPT = """Ekstrak data motor dari teks halaman resmi Honda Big Bike Indonesia di bawah. Balas HANYA dengan satu objek JSON, tanpa teks lain, berkunci:
name (nama model tanpa kata Honda, contoh "CB650R"), body (pilih satu: Naked, Sport, Adventure, Cruiser, Touring, Retro, Lainnya),
engine (Bensin atau Listrik), cc (angka), price (harga termurah di halaman, rupiah, angka; harga tertulis dengan titik sebagai pemisah ribuan, mis. "Rp. 319.914.000" = 319914000),
variants (daftar {{name, price}} bila ada beberapa varian atau warna dengan harga berbeda), speed (km/jam), kmpl (km per liter), acc (detik 0-100 km/jam), range_km (null),
summary (1-2 kalimat Indonesia),
specs (objek berkunci tenaga, torsi, bobot, tinggi_jok, tangki, transmisi, rem, suspensi, ban, fitur; masing-masing teks singkat dengan satuannya, atau null;
 bobot = curb mass, fitur = fitur utama dipisah koma, mis. E-Clutch, RoadSync, lampu LED).
Aturan: isi null untuk data yang TIDAK tertulis di teks. Jangan menebak, menghitung, atau memakai pengetahuan di luar teks.
Abaikan bagian "YOU MIGHT ALSO LIKE" dan "NEWS & COMMUNITY" karena itu motor dan berita lain.
Petunjuk dari alamat: kategori = {kategori}, model = {model}.

TEKS HALAMAN:
"""


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    time.sleep(DELAY)
    return r.text


def find_models():
    soup = BeautifulSoup(get(HOME), "html.parser")
    found = {}
    for a in soup.find_all("a", href=True):
        path = urlparse(urljoin(BASE, a["href"].split("#")[0].split("?")[0])).path.rstrip("/")
        if LINK.match(path):
            found[path] = BASE + path
    return found


def page_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "footer", "noscript"]):
        t.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    return text.split("YOU MIGHT ALSO LIKE")[0][:15000]    # bagian setelahnya = motor dan berita lain


def find_images(html, page_url, slug):
    """Foto utama = foto /photos/ milik model ini; galeri = foto fitur dan galeri (/media/) di halaman model."""
    soup = BeautifulSoup(html, "html.parser")
    key = re.sub(r"[^a-z0-9]", "", re.sub(r"-\d+$", "", re.sub(r"^honda-", "", slug.lower())))
    main, gallery, seen = None, [], set()
    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("src") or ""
        if not src or src.startswith("data:"):
            continue
        url = urljoin(page_url, src.strip())
        path = urlparse(url).path
        flat = re.sub(r"[^a-z0-9]", "", path.rsplit("/", 1)[-1].lower())
        if "/hondabigbike/photos/" in path:
            if key and key in flat and not main:      # foto di blok "produk lain" memuat nama model lain, jadi terbuang
                main = url
            continue
        if "/hondabigbike/media/" in path and url not in seen:
            seen.add(url)
            cap = (img.get("alt") or "").strip()
            hay = cap + " " + path.rsplit("/", 1)[-1]
            tag = next((lab for rx, lab in GTAGS if re.search(rx, hay, re.I)), "Foto")
            gallery.append({"url": url, "caption": cap[:90], "tag": tag})
    return main, gallery[:12]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY belum diatur")
    client = anthropic.Anthropic()
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8")) if OVERRIDES.exists() else {}

    models = find_models()
    print(f"Ditemukan {len(models)} model Big Bike")
    if len(models) < MIN_MODELS:
        sys.exit(f"Hanya {len(models)} model (minimal {MIN_MODELS}). Struktur situs mungkin berubah, data lama dibiarkan.")

    result, new_cache = [], {}
    for path, url in sorted(models.items()):
        cat, sl = path.split("/")[3], path.split("/")[4]      # /hondabigbike/product/<kategori>/<slug>
        try:
            html = get(url)
            text = page_text(html)
            h = hashlib.sha256(text.encode()).hexdigest()
            old = cache.get(url)
            if old and old["hash"] == h:
                data = old["data"]
            else:
                print("Ekstrak:", url)
                r = client.messages.create(model=MODEL, max_tokens=4000, messages=[{"role": "user", "content":
                    PROMPT.format(kategori=cat, model=sl) + text}])
                raw = "".join(b.text for b in r.content if b.type == "text").strip()
                data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
            new_cache[url] = {"hash": h, "data": data}
            image, gallery = find_images(html, url, sl)
        except Exception as e:
            print("GAGAL", url, e)
            if url in cache:
                new_cache[url] = cache[url]
                data, image, gallery = cache[url]["data"], None, []
            else:
                continue
        if not data.get("name") or not data.get("price"):
            print("  dilewati (nama/harga tidak ditemukan):", url)
            continue
        variants = [{"name": v.get("name"), "price": v.get("price")} for v in (data.get("variants") or []) if v.get("name") and v.get("price")]
        specs = {k: str(v) for k, v in (data.get("specs") or {}).items() if v and k in
                 ("tenaga", "torsi", "bobot", "tinggi_jok", "tangki", "transmisi", "rem", "suspensi", "ban", "fitur")}
        item = {
            "id": "honda-" + slug(re.sub(r"^honda-", "", sl)), "brand": "Honda", "name": data["name"],
            "body": data.get("body") or BODY_DEFAULT.get(cat, "Lainnya"), "engine": data.get("engine") or "Bensin",
            "cc": data.get("cc"), "price": data["price"], "price_type": "OTR Jakarta", "variants": variants,
            "model_year": None, "release": None, "speed": data.get("speed"), "kmpl": data.get("kmpl"),
            "acc": data.get("acc"), "range": data.get("range_km"), "sum": data.get("summary") or "", "specs": specs,
            "image": image, "image_credit": "Astra Honda Motor", "gallery": gallery, "trusted_images": True,
            "source": url, "checked": date.today().isoformat(),
        }
        item.update({k: v for k, v in overrides.get(item["id"], {}).items() if v is not None})
        result.append(item)

    if len(result) < MIN_MODELS // 2 + 1:
        sys.exit(f"Hanya {len(result)} motor berhasil diekstrak. Data lama tidak ditimpa. Cek pesan GAGAL di atas.")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(sorted(result, key=lambda x: x["name"]), ensure_ascii=False, indent=1), encoding="utf-8")
    CACHE.write_text(json.dumps(new_cache, ensure_ascii=False), encoding="utf-8")
    print(f"Selesai: {len(result)} motor Big Bike tersimpan; foto utama: {sum(1 for x in result if x['image'])}, galeri: {sum(len(x['gallery']) for x in result)} foto")


if __name__ == "__main__":
    main()
