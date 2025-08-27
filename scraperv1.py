# scrapev1.py
import argparse, os
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0"}
EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

def clean(u: str) -> str:
    u = u.split("__op__")[0]
    u = u.split("?")[0]
    return u

def is_img(u: str) -> bool:
    path = urlparse(u).path.lower()
    return any(path.endswith(ext) for ext in EXTS)

def extract(page_url: str):
    r = requests.get(page_url, headers=UA, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    seen, out = set(), []

    def add(raw):
        if not raw: return
        absu = urljoin(page_url, raw.strip())
        cu = clean(absu)
        if cu not in seen and is_img(cu):
            seen.add(cu)
            out.append(cu)

    # originals in gallery anchors
    for a in soup.select("a[href]"):
        add(a["href"])

    # img tags
    for img in soup.find_all("img"):
        add(img.get("src"))
        ss = img.get("srcset")
        if ss:
            last = ss.split(",")[-1].strip().split()[0]
            add(last)

    return out

def download(urls, outdir):
    os.makedirs(outdir, exist_ok=True)
    for u in urls:
        name = urlparse(u).path.rsplit("/", 1)[-1] or "image"
        path = os.path.join(outdir, name)
        try:
            with requests.get(u, headers=UA, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(path, "wb") as f:
                    for ch in r.iter_content(8192):
                        if ch: f.write(ch)
            print("saved", path)
        except Exception as e:
            print("skip", u, e)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Base image scraper")
    ap.add_argument("url")
    ap.add_argument("-o", "--out", help="download folder")
    args = ap.parse_args()

    imgs = extract(args.url)
    print("\n".join(imgs))
    if args.out:
        download(imgs, args.out)
