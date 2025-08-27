# scrape_from_file_fast.py
import argparse, os, re, time
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0"}
EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

def make_session():
    s = requests.Session()
    s.headers.update(UA)
    retry = Retry(total=3, backoff_factor=0.2, status_forcelist=[429,500,502,503,504])
    adapter = HTTPAdapter(pool_connections=32, pool_maxsize=64, max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s

def clean(u: str) -> str:
    u = u.split("__op__")[0]
    u = u.split("?")[0]
    return u

def is_img(u: str) -> bool:
    path = urlparse(u).path.lower()
    return any(path.endswith(ext) for ext in EXTS)

def extract(page_url: str, session: requests.Session):
    r = session.get(page_url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    seen, out = set(), []

    def add(raw):
        if not raw: return
        cu = clean(urljoin(page_url, raw.strip()))
        if cu not in seen and is_img(cu):
            seen.add(cu); out.append(cu)

    for a in soup.select("a[href]"):
        add(a["href"])
    for img in soup.find_all("img"):
        add(img.get("src"))
        ss = img.get("srcset")
        if ss:
            last = ss.split(",")[-1].strip().split()[0]
            add(last)
    return out

def safe_folder_name(url: str) -> str:
    p = urlparse(url)
    host = p.netloc.replace("www.", "") or "site"
    last = [seg for seg in p.path.split("/") if seg]
    last = last[-1] if last else "home"
    last = last.split(".")[0]
    name = f"{host}_{last}"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-") or "site_home"

def ensure_unique_path(path: str) -> str:
    if not os.path.exists(path): return path
    base, ext = os.path.splitext(path); i = 1
    while os.path.exists(f"{base}_{i}{ext}"): i += 1
    return f"{base}_{i}{ext}"

def download_one(u: str, outdir: str, session: requests.Session):
    fname = urlparse(u).path.rsplit("/", 1)[-1] or "image"
    path = ensure_unique_path(os.path.join(outdir, fname))
    with session.get(u, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for ch in r.iter_content(1024 * 64):
                if ch: f.write(ch)
    return path

def download_many(urls, outdir, session, concurrency=8, limit=None):
    os.makedirs(outdir, exist_ok=True)
    todo = urls[:limit] if limit else urls
    saved, errors = 0, 0
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(download_one, u, outdir, session): u for u in todo}
        for fut in as_completed(futs):
            u = futs[fut]
            try:
                path = fut.result()
                saved += 1
                print("saved", path)
            except Exception as e:
                errors += 1
                print("skip", u, e)
    print(f"done: {saved} saved, {errors} skipped")

def read_urls(file_path: str):
    urls = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls

def run_one(url: str, root: str, session, concurrency, list_only, limit):
    t0 = time.perf_counter()
    imgs = extract(url, session)
    t1 = time.perf_counter()
    folder = os.path.join(root, safe_folder_name(url))
    print(f"\n==> {url}\nfound {len(imgs)} images in {t1 - t0:.2f}s")
    if list_only:
        for u in imgs: print(u)
        return
    if imgs:
        download_many(imgs, folder, session, concurrency=concurrency, limit=limit)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fast image scraper (reads URLs from file, parallel downloads)")
    ap.add_argument("file", help="text file with one URL per line")
    ap.add_argument("--root", default="scrapes", help="root output folder")
    ap.add_argument("--concurrency", type=int, default=8, help="parallel downloads per page")
    ap.add_argument("--list-only", action="store_true", help="only list image URLs (no downloads)")
    ap.add_argument("--limit", type=int, help="max images per page to download")
    args = ap.parse_args()

    session = make_session()
    for url in read_urls(args.file):
        run_one(url, args.root, session, args.concurrency, args.list_only, args.limit)

