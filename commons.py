"""
Cari foto motor di Wikimedia Commons yang berlisensi bebas (CC0, domain publik, CC BY, CC BY-SA)
-> data/commons.json. Nama pembuat foto dan lisensinya disimpan agar bisa dikreditkan di situs.

Foto dari situs lain (media otomotif, blog, forum) sengaja TIDAK diambil karena hak ciptanya milik pemotretnya.
Jalankan:  python commons.py            (hanya motor yang belum dicari)
           python commons.py --refresh  (cari ulang semua)
"""
import json, re, sys, time
from pathlib import Path
import requests

API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "KatalogMotorBot/1.0 (isi-email-kamu@contoh.com)"}   # TODO: isi email kamu (aturan Wikimedia)
DATA = Path(__file__).parent / "data"
OUT = DATA / "commons.json"
SKIP = {"motor.json", "overrides.json", "enriched.json", "research.json", "articles.json", "commons.json"}
LICENSE_OK = re.compile(r"^(cc0|public domain|pd\b|cc by(-sa)?[ -]\d)", re.I)   # menolak NC dan ND
TAGS = [(r"meter|speedo|dash|panel|instrument|cluster", "Panel instrumen"),
        (r"head ?light|head ?lamp|tail ?light|lamp|light|led", "Lampu"),
        (r"suspension|fork|shock|swingarm", "Suspensi"),
        (r"wheel|tire|tyre|rim|brake|disc", "Roda dan rem"),
        (r"exhaust|muffler|silencer", "Knalpot"),
        (r"engine|cylinder|radiator", "Mesin"),
        (r"seat|saddle", "Jok")]
BAD_CAT = re.compile(r"automobile|\bcars?\b|kei car|hatchback|coupe|coupé|sedan|roadster|convertible|cabriolet|truck|minivan|\bsuv\b", re.I)
GOOD_CAT = re.compile(r"motorcycle|motorbike|scooter|moped|underbone|motocross|enduro|supermoto", re.I)
CUT = {"abs", "krt", "se", "edition", "performance", "standard", "special", "plus", "cafe"}
norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())


def model_key(name):
    toks = name.split()
    keep = []
    for i, t in enumerate(toks):
        if i > 0 and t.lower() in CUT:
            break
        keep.append(t)
    return norm(" ".join(keep))


def tag_of(title):
    for rx, label in TAGS:
        if re.search(rx, title, re.I):
            return label
    return "Foto"


def parse_pages(js, brand, name):
    """Saring hasil API: lisensi bebas, relevan dengan model, format foto."""
    key, out = model_key(name), []
    for p in (js.get("query", {}).get("pages", {}) or {}).values():
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        title = p.get("title", "")
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        cats = " ".join(x.get("title", "") for x in p.get("categories", []))
        if BAD_CAT.search(cats) and not GOOD_CAT.search(cats):      # mis. "Honda Beat" yang ternyata mobil
            continue
        if not (LICENSE_OK.match(lic) and info.get("mime") in ("image/jpeg", "image/png") and key in norm(title)):
            continue
        author = re.sub(r"<[^>]+>", "", (meta.get("Artist") or {}).get("value", "")).strip() or "Kontributor Wikimedia"
        out.append({"url": info.get("thumburl") or info.get("url"), "page": info.get("descriptionurl"),
                    "author": author[:80], "license": lic, "license_url": (meta.get("LicenseUrl") or {}).get("value"),
                    "tag": tag_of(title), "title": re.sub(r"^File:|\.\w+$", "", title)})
    return out[:8]


def search(brand, name):
    r = requests.get(API, headers=HEADERS, timeout=30, params={
        "action": "query", "format": "json", "generator": "search", "gsrsearch": f"{brand} {name}",
        "gsrnamespace": 6, "gsrlimit": 20, "prop": "imageinfo|categories", "iiprop": "url|extmetadata|mime", "cllimit": "max", "clshow": "!hidden",
        "iiurlwidth": 800})
    r.raise_for_status()
    return parse_pages(r.json(), brand, name)


def main():
    refresh = "--refresh" in sys.argv
    done = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    motors = []
    for f in sorted(DATA.glob("*.json")):
        if f.name not in SKIP and not f.name.startswith("."):
            motors += json.loads(f.read_text(encoding="utf-8"))
    todo = [m for m in motors if refresh or m["id"] not in done]
    print(f"{len(todo)} motor dicari di Wikimedia Commons")
    for m in todo:
        try:
            done[m["id"]] = search(m["brand"], m["name"])
        except Exception as e:
            print("GAGAL", m["name"], e)
            continue
        print(f"{m['brand']} {m['name']}: {len(done[m['id']])} foto")
        OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
        time.sleep(1)


if __name__ == "__main__":
    main()
