# scrape_from_file.py
import argparse, os, re
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
        if not raw:
            return
        cu = clean(urljoin(page_url, raw.strip()))
        if cu not in seen and is_img(cu):
            seen.add(cu)
            out.append(cu)

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
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
    return name or "site_home"

def ensure_unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(f"{base}_{i}{ext}"):
        i += 1
    return f"{base}_{i}{ext}"

def download(urls, outdir):
    os.makedirs(outdir, exist_ok=True)
    for u in urls:
        fname = urlparse(u).path.rsplit("/", 1)[-1] or "image"
        path = ensure_unique_path(os.path.join(outdir, fname))
        try:
            with requests.get(u, headers=UA, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(path, "wb") as f:
                    for ch in r.iter_content(8192):
                        if ch:
                            f.write(ch)
            print("saved", path)
        except Exception as e:
            print("skip", u, e)

def read_urls(file_path: str):
    urls = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)
    return urls

def run_one(url: str, root: str):
    folder = os.path.join(root, safe_folder_name(url))
    imgs = extract(url)
    if not imgs:
        print("no images found for", url)
        return
    download(imgs, folder)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Scrape images from list of URLs, auto folder per URL")
    ap.add_argument("file", help="text file with one URL per line")
    ap.add_argument("--root", default="scrapes", help="root output folder")
    args = ap.parse_args()

    urls = read_urls(args.file)
    if not urls:
        print("no urls found in", args.file)
    for url in urls:
        print("\n==>", url)
        run_one(url, args.root)
