"""
Pengumpul data motor Suzuki Indonesia -> data/suzuki.json

Situs suzuki.co.id dibuat di server, jadi cukup dibaca dengan requests + BeautifulSoup:
  /motorcycle           daftar motor (juga ada di menu "Motorcycle")
  /motorcycle/<slug>    halaman satu motor: ringkasan, fitur + foto fitur, pilihan warna, harga OTR Jakarta, tabel spesifikasi
Motor yang tidak ada lagi di daftar resmi otomatis hilang (= tidak dijual lagi).
Jalankan:  ANTHROPIC_API_KEY=... python scrape_suzuki.py
"""
import hashlib, json, os, re, sys, time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import anthropic

BASE = "https://www.suzuki.co.id"
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
MIN_MODELS = 5
DELAY = 1.5
ROOT = Path(__file__).parent
OUT = ROOT / "data" / "suzuki.json"
CACHE = ROOT / "data" / ".cache_suzuki.json"
OVERRIDES = ROOT / "data" / "overrides.json"
HEADERS = {"User-Agent": "KatalogMotorBot/1.0 (+kontak: isi-email-kamu)"}
PRODUCT = re.compile(r"^/motorcycle/([^/]+)$")
SKIP_SLUGS = {"simulasi-kredit"}
BODY_BY_SLUG = {"satria": "Bebek", "gsx-r150": "Sport", "gsx-s150": "Naked", "v-strom-250sx": "Adventure"}   # selain itu: Matic
GTAGS = [(r"meter|speedo|dash|panel|instrument|cluster|lcd|tft", "Panel instrumen"), (r"head ?light|head ?lamp|tail|lamp|light|led|winker|hazard", "Lampu"),
         (r"suspen|fork|shock|swingarm", "Suspensi"), (r"wheel|tire|tyre|velg|rim|brake|disc|abs|ban ", "Roda dan rem"),
         (r"exhaust|muffler|knalpot|silencer", "Knalpot"), (r"engine|mesin|cylinder|radiator|dohc", "Mesin"),
         (r"seat|jok|saddle|luggage|bagasi|u-?box", "Jok dan bagasi")]

PROMPT = """Ekstrak data motor dari teks halaman resmi Suzuki Indonesia di bawah. Balas HANYA dengan satu objek JSON, tanpa teks lain, berkunci:
name (nama model tanpa kata Suzuki; bila satu halaman memuat dua model, tulis keduanya, mis. "Satria Pro / Satria F150"),
body (pilih satu: Matic, Bebek, Sport, Naked, Trail, Retro, Adventure, Supermoto, Lainnya),
engine (Bensin atau Listrik), cc (angka), price (harga varian termurah, rupiah, angka; "Rp 36.350.000" = 36350000),
variants (daftar {{name, price}} semua varian yang punya harga di bagian "Harga ... OTR Jakarta"; varian yang hanya beda warna dan harganya sama cukup ditulis satu kali),
speed (km/jam), kmpl (km per liter), acc (detik 0-100 km/jam), range_km (null), summary (1-2 kalimat Indonesia),
specs (objek berkunci tenaga, torsi, bobot, tinggi_jok, tangki, transmisi, rem, suspensi, ban, fitur; masing-masing teks singkat dengan satuannya atau null;
 tenaga = Tenaga Maksimum, bobot = Berat Kosong atau berat isi, rem = rem depan dan belakang, suspensi = depan dan belakang, ban = roda depan dan belakang, fitur = fitur utama dipisah koma).
Aturan: isi null untuk data yang TIDAK tertulis di teks. Jangan menebak, menghitung, atau memakai pengetahuan di luar teks.
Hanya pakai data motor pada halaman ini (bukan daftar motor lain).
Petunjuk: alamat = {slug}; judul halaman = {judul}.

TEKS HALAMAN:
"""


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    time.sleep(DELAY)
    return r.text


def list_products():
    soup = BeautifulSoup(get(BASE + "/motorcycle"), "html.parser")
    found = []
    for a in soup.find_all("a", href=True):
        m = PRODUCT.match(urlparse(urljoin(BASE, a["href"].split("#")[0].split("?")[0])).path)
        if m and m.group(1) not in SKIP_SLUGS and m.group(1) not in found:
            found.append(m.group(1))
    return found


def page_text(soup):
    s = BeautifulSoup(str(soup), "html.parser")
    for t in s(["script", "style", "nav", "noscript"]):
        t.decompose()
    text = re.sub(r"[ \t]+", " ", s.get_text("\n"))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    a = text.find("Lainnya Untuk Anda")             # blok motor Suzuki lain (jangan campur dengan motor ini)
    b = text.find("Test Ride Motor", a) if a > 0 else -1
    if a > 0 and b > a:
        text = text[:a] + text[b:]
    return text[:20000]


def images_of(soup, page_url):
    """Foto motor, foto fitur (dengan judul fitur), banner, dan foto pilihan warna dari halaman ini."""
    main, gallery, seen = None, [], set()
    colors = 0
    for img in soup.find_all("img"):
        s = (img.get("data-src") or img.get("src") or "").strip()
        if not s or s.startswith("data:"):
            continue
        url = urljoin(page_url, s)
        path = urlparse(url).path
        name = path.rsplit("/", 1)[-1].lower()
        alt = (img.get("alt") or img.get("title") or "").replace("&amp;", "&").strip()
        if url in seen:
            continue
        if "/img/motorcycle/" in path and "/revamp/" not in path:
            if re.search(r"logo|bg-color|icon|thumbnail", name):
                continue
            seen.add(url)
            if name.startswith("feature"):
                tag = next((lab for rx, lab in GTAGS if re.search(rx, alt + " ", re.I)), "Foto")
                gallery.append({"url": url, "caption": alt[:90], "tag": tag})
            elif "banner" in name:
                gallery.append({"url": url, "caption": alt[:90] or "Foto", "tag": "Foto"})
            elif not main:
                main = url
            else:
                gallery.append({"url": url, "caption": alt[:90] or "Foto", "tag": "Foto"})
        elif "/assets/static/img/variant/" in path and colors < 6:
            seen.add(url)
            colors += 1
            gallery.append({"url": url, "caption": alt[:90] or "Pilihan warna", "tag": "Foto"})
    if not main:
        main = next((g["url"] for g in gallery if "variant" in g["url"]), None)
    return main, gallery[:16]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY belum diatur")
    client = anthropic.Anthropic()
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8")) if OVERRIDES.exists() else {}

    slugs = list_products()
    print(f"Ditemukan {len(slugs)} model motor Suzuki")
    if len(slugs) < MIN_MODELS:
        sys.exit(f"Hanya {len(slugs)} model (minimal {MIN_MODELS}). Struktur situs mungkin berubah, data lama dibiarkan.")

    result, new_cache = [], {}
    for sl in slugs:
        url = f"{BASE}/motorcycle/{sl}"
        try:
            soup = BeautifulSoup(get(url), "html.parser")
            text = page_text(soup)
            title = soup.title.get_text(strip=True) if soup.title else ""
            h = hashlib.sha256(text.encode()).hexdigest()
            old = cache.get(sl)
            if old and old["hash"] == h:
                data = old["data"]
            else:
                print("Ekstrak:", sl)
                r = client.messages.create(model=MODEL, max_tokens=4000, messages=[{"role": "user", "content":
                    PROMPT.format(slug=sl, judul=title) + text}])
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
            "id": "suzuki-" + slug(sl), "brand": "Suzuki", "name": data["name"],
            "body": BODY_BY_SLUG.get(sl) or ("Matic" if (data.get("body") in (None, "Lainnya")) else data["body"]),
            "engine": data.get("engine") or "Bensin", "cc": data.get("cc"), "price": data["price"], "price_type": "OTR Jakarta",
            "variants": variants, "model_year": None, "release": None, "speed": data.get("speed"), "kmpl": data.get("kmpl"),
            "acc": data.get("acc"), "range": data.get("range_km"), "sum": data.get("summary") or "", "specs": specs,
            "image": image, "image_credit": "Suzuki Indonesia", "gallery": gallery, "trusted_images": True,
            "source": url, "checked": date.today().isoformat(),
        }
        item.update({k: v for k, v in overrides.get(item["id"], {}).items() if v is not None})
        result.append(item)

    if len(result) < max(3, MIN_MODELS // 2):
        sys.exit(f"Hanya {len(result)} motor berhasil diekstrak. Data lama tidak ditimpa. Cek pesan GAGAL di atas.")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(sorted(result, key=lambda x: x["name"]), ensure_ascii=False, indent=1), encoding="utf-8")
    CACHE.write_text(json.dumps(new_cache, ensure_ascii=False), encoding="utf-8")
    print(f"Selesai: {len(result)} motor Suzuki tersimpan; foto utama: {sum(1 for x in result if x['image'])}, galeri: {sum(len(x['gallery']) for x in result)} foto")


if __name__ == "__main__":
    main()
