# scraperv4.py
import argparse, os, re, time
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from playwright.sync_api import sync_playwright

# ---------- config ----------
UA_STR = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
UA = {"User-Agent": UA_STR}
EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

# ---------- http session for downloads ----------
def make_session():
    s = requests.Session()
    s.headers.update(UA)
    retry = Retry(total=3, backoff_factor=0.2, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(pool_connections=32, pool_maxsize=64, max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s

# ---------- small helpers ----------
def clean(u: str) -> str:
    u = u.split("__op__")[0]
    u = u.split("?")[0]
    return u

def is_img(u: str) -> bool:
    path = urlparse(u).path.lower()
    return any(path.endswith(ext) for ext in EXTS)

def normalize_to_img(raw: str, base: str):
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("//"):               # protocol-relative
        raw = "https:" + raw
    absu = urljoin(base, raw)
    cu = clean(absu)
    return cu if is_img(cu) else None

# ---------- playwright extraction tuned for bambulab.com ----------
def extract(page_url: str):
    """
    Load page with Playwright and collect:
      - <img src>, <img srcset>
      - <picture><source srcset>
      - common lazy attrs: data-src, data-original, data-lazy, data-bg, data-background, data-srcset
      - CSS background-image URLs
    Auto-scrolls to trigger lazy loading.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA_STR, viewport={"width": 1366, "height": 900})
        page = ctx.new_page()

        # Less strict wait (network never truly idles on this site)
        page.goto(page_url, timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        # Auto-scroll to trigger lazy-loading
        page.evaluate(r"""
            async () => {
                const step = 800;
                for (let i = 0; i < 30; i++) {
                    window.scrollBy(0, step);
                    await new Promise(r => setTimeout(r, 250));
                    const bottom = window.scrollY + window.innerHeight + 5;
                    if (bottom >= document.body.scrollHeight) break;
                }
            }
        """)
        page.wait_for_timeout(1500)

        # Collect URLs in the browser context (fast)
        raw_urls = page.evaluate(r"""
            () => {
                const urls = new Set();

                // <img src> and srcset
                document.querySelectorAll("img").forEach(img => {
                    if (img.currentSrc) urls.add(img.currentSrc);
                    if (img.src) urls.add(img.src);
                    const ss = img.getAttribute("srcset");
                    if (ss) ss.split(",").forEach(p => {
                        const u = p.trim().split(/\s+/)[0];
                        if (u) urls.add(u);
                    });
                });

                // <picture><source srcset>
                document.querySelectorAll("picture source").forEach(s => {
                    const ss = s.getAttribute("srcset");
                    if (ss) ss.split(",").forEach(p => {
                        const u = p.trim().split(/\s+/)[0];
                        if (u) urls.add(u);
                    });
                });

                // common lazy-load attributes
                const ATTRS = ["data-src", "data-original", "data-lazy", "data-bg", "data-background", "data-srcset"];
                document.querySelectorAll("*").forEach(el => {
                    for (const a of ATTRS) {
                        const v = el.getAttribute(a);
                        if (!v) continue;
                        if (a.endsWith("srcset")) {
                            v.split(",").forEach(p => {
                                const u = p.trim().split(/\s+/)[0];
                                if (u) urls.add(u);
                            });
                        } else {
                            urls.add(v);
                        }
                    }
                    const bg = getComputedStyle(el).backgroundImage;
                    if (bg && bg.startsWith("url(")) {
                        const m = bg.match(/url\\(["']?([^"')]+)["']?\\)/);
                        if (m && m[1]) urls.add(m[1]);
                    }
                });

                return Array.from(urls);
            }
        """)

        ctx.close()
        browser.close()

    # Normalize, filter to images, dedupe
    seen, out = set(), []
    for r in raw_urls:
        u = normalize_to_img(r, page_url)
        if u and u not in seen:
            seen.add(u); out.append(u)
    return out

# ---------- downloading (unchanged logic) ----------
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
                print("saved", fut.result()); saved += 1
            except Exception as e:
                print("skip", u, e); errors += 1
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
    imgs = extract(url)
    t1 = time.perf_counter()
    folder = os.path.join(root, safe_folder_name(url))
    print(f"\n==> {url}\nfound {len(imgs)} images in {t1 - t0:.2f}s")
    if list_only:
        print("\n".join(imgs))
        return
    if imgs:
        download_many(imgs, folder, session, concurrency=concurrency, limit=limit)

# ---------- cli ----------
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Image scraper v4 (Playwright, tuned for bambulab.com)")
    ap.add_argument("file", help="text file with one URL per line")
    ap.add_argument("--root", default="scrapes", help="root output folder")
    ap.add_argument("--concurrency", type=int, default=8, help="parallel downloads per page")
    ap.add_argument("--list-only", action="store_true", help="only list image URLs (no downloads)")
    ap.add_argument("--limit", type=int, help="max images per page to download")
    args = ap.parse_args()

    session = make_session()
    for url in read_urls(args.file):
        run_one(url, args.root, session, args.concurrency, args.list_only, args.limit)
