"""
Pengumpul data motor listrik dari banyak merek -> data/ev.json

Tiap merek cukup didaftarkan di ev_brands.json (nama merek + alamat awal situsnya). Skrip ini lalu:
  1. membuka halaman awal dan mengumpulkan link,
  2. meminta Claude memilih link yang merupakan halaman produk motor listrik (dan halaman daftar yang layak dibuka),
  3. membaca tiap halaman produk dan meminta Claude mengekstrak data (tanpa menebak),
  4. mengambil foto dari halaman (foto diperiksa kemudian oleh verify_images.py).

Cek alamat merek tanpa kunci API:  python scrape_ev.py --check
Jalankan sungguhan:                ANTHROPIC_API_KEY=... python scrape_ev.py
"""
import hashlib, json, os, re, sys, time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
DELAY = 1.5
MAX_PRODUCTS = 25
ROOT = Path(__file__).parent
BRANDS = ROOT / "ev_brands.json"
OUT = ROOT / "data" / "ev.json"
CACHE = ROOT / "data" / ".cache_ev.json"
OVERRIDES = ROOT / "data" / "overrides.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; KatalogMotorBot/1.0; +kontak: isi-email-kamu)"}
SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|webp|gif|svg|zip|mp4)(\?|$)", re.I)
SKIP_HOST = re.compile(r"facebook|instagram|youtube|tiktok|twitter|x\.com|wa\.me|whatsapp|linkedin|google|play\.google|apps\.apple", re.I)
JUNK_IMG = re.compile(r"logo|icon|sprite|avatar|flag|facebook|instagram|youtube|tiktok|twitter|\.svg|\.gif|placeholder|loading|arrow|btn|button|banner-promo", re.I)
GTAGS = [(r"meter|speedo|dash|panel|instrument|cluster|lcd|tft", "Panel instrumen"), (r"head ?light|head ?lamp|tail|lamp|light|led|lampu", "Lampu"),
         (r"suspen|fork|shock", "Suspensi"), (r"wheel|tire|tyre|velg|rim|brake|disc|rem|ban ", "Roda dan rem"),
         (r"battery|baterai|charger|charging", "Baterai"), (r"motor|hub|dinamo|mesin", "Mesin"), (r"seat|jok|saddle|bagasi|luggage", "Jok dan bagasi")]

PICK_PROMPT = """Berikut daftar link dari situs resmi merek motor listrik "{brand}" di Indonesia (format: teks link | alamat).
Pilih alamat yang berupa:
- "produk": halaman detail SATU model motor atau skuter listrik roda dua (bukan sepeda listrik, roda tiga, mobil, baterai, charger, aksesori, berita, promo, dealer, atau layanan).
- "daftar": halaman katalog yang memuat banyak model motor listrik dan layak dibuka untuk mencari halaman produk.
Hanya pilih alamat yang ADA di daftar. Balas HANYA JSON: {{"produk": ["url", ...], "daftar": ["url", ...]}}

LINK:
{links}"""

EXTRACT_PROMPT = """Ekstrak data motor listrik dari teks halaman resmi merek {brand} di bawah. Balas HANYA dengan satu objek JSON, tanpa teks lain, berkunci:
name (nama model tanpa nama merek), body (pilih satu: Matic untuk skuter, Bebek, Sport, Naked, Retro, Lainnya),
cc (isi 0), price (harga termurah yang tertulis, rupiah, angka; "Rp 19.900.000" = 19900000), price_type (jenis harga bila tertulis, mis. "OTR Jakarta"; bila ada skema berbeda, sebutkan skema untuk harga termurah, mis. "OTR Jakarta, skema sewa baterai"; jika tidak tertulis isi null),
variants (daftar {{name, price}} bila ada beberapa varian atau skema berharga, mis. "Beli dengan baterai" dan "Langganan baterai"), speed (kecepatan maksimum km/jam), kmpl (isi null), acc (detik 0-100 km/jam), range_km (jarak tempuh km per sekali pengisian penuh),
summary (1-2 kalimat Indonesia),
specs (objek berkunci tenaga (daya motor listrik), torsi, bobot, tinggi_jok, baterai (jenis dan kapasitas), pengisian (waktu atau jenis pengisian daya), rem, suspensi, ban, fitur;
 masing-masing teks singkat dengan satuannya atau null).
Aturan: isi null untuk data yang TIDAK tertulis di teks. Jangan menebak, menghitung, atau memakai pengetahuan di luar teks.
Jika halaman ini BUKAN halaman satu model motor atau skuter listrik roda dua (misalnya sepeda listrik, roda tiga, mobil, daftar, berita), balas {{"skip": true}}.
Alamat halaman: {url}

TEKS HALAMAN:
"""


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    time.sleep(DELAY)
    return r.text, r.url


def links_of(html, page_url, host):
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        u = urljoin(page_url, a["href"].split("#")[0].strip())
        p = urlparse(u)
        if p.scheme not in ("http", "https") or SKIP_EXT.search(u) or SKIP_HOST.search(p.netloc):
            continue
        if p.netloc.replace("www.", "") != host:
            continue
        u = u.rstrip("/")
        if u in seen or u == page_url.rstrip("/"):
            continue
        seen.add(u)
        out.append((re.sub(r"\s+", " ", a.get_text(" ", strip=True))[:60], u))
    return out


def page_text(soup):
    s = BeautifulSoup(str(soup), "html.parser")
    for t in s(["script", "style", "nav", "footer", "noscript"]):
        t.decompose()
    text = re.sub(r"[ \t]+", " ", s.get_text("\n"))
    return re.sub(r"\n\s*\n+", "\n", text).strip()[:15000]


def images_of(soup, page_url):
    og = soup.find("meta", attrs={"property": "og:image"})
    image = urljoin(page_url, og["content"]) if og and og.get("content") else None
    s = BeautifulSoup(str(soup), "html.parser")
    for t in s(["nav", "header", "footer", "aside", "script", "style"]):
        t.decompose()
    gallery, seen = [], set()
    for img in (s.find("main") or s.body or s).find_all("img"):
        src = (img.get("data-src") or img.get("data-lazy-src") or img.get("src") or "").strip()
        if not src or src.startswith("data:") or JUNK_IMG.search(src):
            continue
        url = urljoin(page_url, src)
        if url in seen:
            continue
        seen.add(url)
        cap = (img.get("alt") or img.get("title") or "").strip()
        tag = next((lab for rx, lab in GTAGS if re.search(rx, cap + " " + url.rsplit("/", 1)[-1], re.I)), "Foto")
        gallery.append({"url": url, "caption": cap[:90], "tag": tag})
    return image, gallery[:12]


def ask_json(client, prompt, max_tokens=4000):
    r = client.messages.create(model=MODEL, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    raw = "".join(b.text for b in r.content if b.type == "text")
    return json.loads(re.search(r"\{.*\}", raw, re.S).group(0))


def pick_links(client, brand, links, cache):
    lines = "\n".join(f"{t} | {u}" for t, u in links[:250])
    key = "pick:" + hashlib.sha256(lines.encode()).hexdigest()
    if key not in cache:
        res = ask_json(client, PICK_PROMPT.format(brand=brand, links=lines), 2000)
        known = {u for _, u in links}
        cache[key] = {"produk": [u for u in res.get("produk", []) if u in known], "daftar": [u for u in res.get("daftar", []) if u in known]}
    return cache[key]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def check():
    for b in json.loads(BRANDS.read_text(encoding="utf-8")):
        for u in b["start"]:
            try:
                html, final = get(u)
                host = urlparse(final).netloc.replace("www.", "")
                soup = BeautifulSoup(html, "html.parser")
                ls = links_of(html, final, host)
                print(f"[OK ] {b['brand']}: {final} | judul: {soup.title.get_text(strip=True)[:60] if soup.title else '-'} | {len(ls)} link internal | {len(html)} karakter")
                for t, l in ls[:12]:
                    print("        ", t[:30], "|", l)
            except Exception as e:
                print(f"[GAGAL] {b['brand']}: {u} -> {e}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")      # Windows: cegah error 'charmap' saat mencetak karakter khusus
    except Exception:
        pass
    if "--check" in sys.argv:
        return check()
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY belum diatur")
    import anthropic
    client = anthropic.Anthropic()
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8")) if OVERRIDES.exists() else {}
    result, new_cache = [], {}
    for b in json.loads(BRANDS.read_text(encoding="utf-8")):
        brand = b["brand"]
        try:
            host, links, visited = None, [], set()
            for u in b["start"]:
                html, final = get(u)
                host = host or urlparse(final).netloc.replace("www.", "")
                visited.add(final.rstrip("/"))
                links += links_of(html, final, host)
            pick = pick_links(client, brand, links, cache)
            produk = list(pick["produk"])
            for lst in [u for u in pick["daftar"] if u not in visited][:4]:      # putaran kedua: buka halaman daftar
                try:
                    html, final = get(lst)
                    visited.add(lst)
                    more = links_of(html, final, host)
                    produk += pick_links(client, brand, more, cache)["produk"]
                except Exception as e:
                    print(f"  [{brand}] halaman daftar gagal: {lst} ({e})")
            produk = list(dict.fromkeys(produk))[:MAX_PRODUCTS]
            print(f"[{brand}] {len(produk)} halaman produk dipilih")
        except Exception as e:
            print(f"[{brand}] GAGAL membuka situs: {e}")
            continue
        for url in produk:
            try:
                html, final = get(url)
                soup = BeautifulSoup(html, "html.parser")
                text = page_text(soup)
                h = hashlib.sha256(text.encode()).hexdigest()
                old = cache.get(url)
                if old and old.get("hash") == h:
                    data = old["data"]
                else:
                    print("  Ekstrak:", url)
                    data = ask_json(client, EXTRACT_PROMPT.format(brand=brand, url=url) + text)
                new_cache[url] = {"hash": h, "data": data}
                image, gallery = images_of(soup, final)
            except Exception as e:
                print("  GAGAL", url, e)
                if url in cache and "data" in cache[url]:
                    new_cache[url] = cache[url]
                    data, image, gallery = cache[url]["data"], None, []
                else:
                    continue
            if data.get("skip") or not data.get("name") or not data.get("price"):
                print("  dilewati (bukan halaman model / tanpa harga):", url)
                continue
            variants = [{"name": v.get("name"), "price": v.get("price")} for v in (data.get("variants") or []) if v.get("name") and v.get("price")]
            specs = {k: str(v) for k, v in (data.get("specs") or {}).items() if v and k in
                     ("tenaga", "torsi", "bobot", "tinggi_jok", "baterai", "pengisian", "rem", "suspensi", "ban", "fitur")}
            item = {
                "id": f"{slug(brand)}-{slug(data['name'])}", "brand": brand, "name": data["name"],
                "body": data.get("body") or "Matic", "engine": "Listrik", "cc": 0, "price": data["price"],
                "price_type": data.get("price_type") or "Harga resmi (jenis harga tidak dijelaskan situs)",
                "variants": variants, "model_year": None, "release": None, "speed": data.get("speed"), "kmpl": None,
                "acc": data.get("acc"), "range": data.get("range_km"), "sum": data.get("summary") or "", "specs": specs,
                "image": image, "image_credit": brand, "gallery": gallery, "source": url, "checked": date.today().isoformat(),
            }
            item.update({k: v for k, v in overrides.get(item["id"], {}).items() if v is not None})
            if all(x["id"] != item["id"] for x in result):
                result.append(item)
    if not result:
        sys.exit("Tidak ada motor listrik yang berhasil diekstrak. Data lama tidak ditimpa. Jalankan: python scrape_ev.py --check")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(sorted(result, key=lambda x: (x["brand"], x["name"])), ensure_ascii=False, indent=1), encoding="utf-8")
    CACHE.write_text(json.dumps({**cache, **new_cache}, ensure_ascii=False), encoding="utf-8")
    per = {}
    for x in result:
        per[x["brand"]] = per.get(x["brand"], 0) + 1
    print(f"Selesai: {len(result)} motor listrik ->", ", ".join(f"{k} {v}" for k, v in per.items()))


if __name__ == "__main__":
    main()
