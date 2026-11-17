"""A-share announcements that are scanned images (no text layer): download again and read them with free OCR.

Frank 2026-11-17: "那些扫描件...用方法把它们下载读一遍". About 13,000 of 1.29 million announcements (1%) failed text
extraction with "empty text (scanned PDF?)". This job finds them in the text caches (data/cn_ann_text, data/cn_ann_big),
downloads each PDF, renders every page (pdftoppm, 200 dpi) and reads it with tesseract (Simplified Chinese + English).
Results go to data/cn_ann_ocr/<code>.json.gz as {announcement id: {t, title, kind, pages, text, full, ocr: True}},
the same shape as the text cache, so readers just overlay it.

Env: OCR_SHARD "k/N" (every N-th stock), OCR_MINUTES (time budget, default 300), OCR_DPI (default 200).
Polite to cninfo like cn_ann_text: one PDF at a time per lane, backoff on 403/429/503, stop after 20 refusals.
"""
import gzip
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "http://static.cninfo.com.cn/"
SRC = [Path("data/cn_ann_text"), Path("data/cn_ann_big")]
IDX = Path("data/cn_ann")
OUT = Path("data/cn_ann_ocr")
BUDGET = 60 * float(os.environ.get("OCR_MINUTES", "300"))
DPI = os.environ.get("OCR_DPI", "200")
MAX_PAGES = 400
BRAKE = {"hits": 0}


def download(url, path):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(BASE + url, headers={"Accept": "application/pdf"}), timeout=300) as r, \
                    open(path, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            return
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 503):
                BRAKE["hits"] += 1
                time.sleep(min(600, 30 * 2 ** min(BRAKE["hits"], 5)))
            if attempt == 2:
                raise
        except Exception:
            if attempt == 2:
                raise
            time.sleep(10)


def ocr_page(png):
    r = subprocess.run(["tesseract", str(png), "-", "-l", "chi_sim+eng", "--psm", "3"],
                       capture_output=True, timeout=300, env={**os.environ, "OMP_THREAD_LIMIT": "1"})
    t = r.stdout.decode("utf-8", "replace")
    t = re.sub(r"(?<=[一-鿿]) (?=[一-鿿])", "", t)   # tesseract puts spaces between Chinese characters
    return re.sub(r"[ \t]+", " ", t).strip()


def ocr_pdf(url, pool):
    with tempfile.TemporaryDirectory() as d:
        pdf = Path(d) / "a.pdf"
        download(url, pdf)
        info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=120).stdout
        n = int((re.search(r"Pages:\s+(\d+)", info) or [0, 0])[1])
        subprocess.run(["pdftoppm", "-r", DPI, "-gray", "-png", "-l", str(min(n or MAX_PAGES, MAX_PAGES)), str(pdf), str(Path(d) / "p")],
                       capture_output=True, timeout=3600)
        pngs = sorted(Path(d).glob("p-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
        texts = list(pool.map(ocr_page, pngs))
    text = "\n".join(f"[第{i + 1}页]\n{t}" for i, t in enumerate(texts) if t)
    return text, n


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    k, n = (int(x) for x in os.environ.get("OCR_SHARD", "0/1").split("/"))
    codes = sorted(p.name[:-8] for p in SRC[0].glob("*.json.gz"))   # the lane restores one text-cache shard: its stocks
    codes = [c for i, c in enumerate(codes) if i % n == k]
    mine = {c + ".json.gz" for c in codes}
    for g in OUT.glob("*.json.gz"):   # this lane's cache keeps only its own stocks
        if g.name not in mine:
            g.unlink()
    urls = {}
    stats = {"lane": f"{k}/{n}", "scanned": 0, "done": 0, "ocr_now": 0, "pages_now": 0, "failed_now": 0, "left": 0, "by_year": {}}
    pool = ThreadPoolExecutor(max_workers=os.cpu_count() or 4)
    for code in codes:
        todo = []
        for s in SRC:
            p = s / f"{code}.json.gz"
            if p.exists():
                for i, r in json.loads(gzip.decompress(p.read_bytes())).items():
                    if r.get("error") == "ValueError" and "empty text" in (r.get("msg") or "empty text"):
                        todo.append((i, r))
        if not todo:
            continue
        stats["scanned"] += len(todo)
        for i, r in todo:
            y = r.get("t", "")[:4]
            stats["by_year"][y] = stats["by_year"].get(y, 0) + 1
        op = OUT / f"{code}.json.gz"
        have = json.loads(gzip.decompress(op.read_bytes())) if op.exists() else {}
        todo = [(i, r) for i, r in todo if i not in have or (have[i].get("error") and have[i].get("tries", 1) < 2)]
        if not todo:
            continue
        if time.time() - t0 > BUDGET or BRAKE["hits"] >= 20:
            stats["left"] += len(todo)
            continue
        if code not in urls:   # announcement urls live in the index
            ip = IDX / f"{code}.json.gz"
            urls[code] = {str(a["id"]): a["url"] for a in json.loads(gzip.decompress(ip.read_bytes()))} if ip.exists() else {}
        for j, (i, r) in enumerate(todo):
            if time.time() - t0 > BUDGET or BRAKE["hits"] >= 20:
                stats["left"] += len(todo) - j
                break
            rec = {"t": r.get("t"), "title": r.get("title"), "kind": r.get("kind"), "ocr": True}
            try:
                text, pages = ocr_pdf(urls[code][i], pool)
                if len(text) < 30:
                    raise ValueError("OCR found no text")
                rec.update(pages=pages, text=text, full=True)
                stats["ocr_now"] += 1
                stats["pages_now"] += pages
            except Exception as e:
                rec.update(error=type(e).__name__, msg=str(e)[:120], tries=have.get(i, {}).get("tries", 0) + 1)
                stats["failed_now"] += 1
            have[i] = rec
            op.write_bytes(gzip.compress(json.dumps(have, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "replace")))
            time.sleep(0.5)
    stats["minutes"] = round((time.time() - t0) / 60, 1)
    Path("ocr_left.txt").write_text(str(stats["left"]))
    print("[cn_ocr]", json.dumps({k: v for k, v in stats.items() if k != "by_year"}), "years:",
          json.dumps(dict(sorted(stats["by_year"].items()))))


if __name__ == "__main__":
    main()
