"""Round 67 (pilot): download recent ASX announcements (PDF -> text) for the current rocket candidates and any
extra codes, so they can be read and turned into catalyst features (cash runway, permits, discoveries, raisings,
insider buying). Writes out/ann_pilot.json. Only free ASX pages are used.

Env: ANN_CODES (extra codes, comma separated), ANN_PER_CODE (default 12), ANN_MONTHS (default 12).
"""
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from html import unescape
from pathlib import Path

LIST = "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode={c}&timeframe=Y&year={y}"
HDR = {"Accept": "text/html,application/pdf,*/*"}
calls = {"list": 0, "pdf": 0, "failed": 0}


# Brake (2026-11-15): if ASX starts refusing us (403/429/503), slow right down; after 20 refusals in one run, stop the
# run cleanly (progress is saved) so we never get locked out. The workflow then waits an hour before the next run.
BRAKE = {"hits": 0, "stop": False}
_brake_lock = __import__("threading").Lock()


def get(url, timeout=40):
    req = urllib.request.Request(url, headers=HDR)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), r.geturl(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429, 503):
            with _brake_lock:
                BRAKE["hits"] += 1
                BRAKE["stop"] = BRAKE["stop"] or BRAKE["hits"] >= 20
                wait = min(600, 30 * 2 ** (min(BRAKE["hits"], 5) - 1))
                print(f"  ASX refused ({e.code}), refusal #{BRAKE['hits']}, pausing {wait}s", flush=True)
                time.sleep(wait)
        raise


def listing(code, years):
    items = []
    for y in years:
        try:
            html = get(LIST.format(c=code, y=y))[0].decode("utf-8", "replace")
            calls["list"] += 1
        except Exception as e:
            calls["failed"] += 1
            print("  list failed", code, y, type(e).__name__)
            continue
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S):
            m = re.search(r"(\d{2})/(\d{2})/(\d{4})", tr)
            link = re.search(r'href="([^"]*idsId=(\d+)[^"]*)"', tr)
            if not m or not link:
                continue
            text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", tr))).strip()
            head = re.sub(r"^\d{2}/\d{2}/\d{4}\s*(\d{1,2}:\d{2}\s*[ap]m)?\s*", "", text, flags=re.I)
            head = re.sub(r"\s*\d+\s*pages?\s*[\d.]+\s*[KM]B\s*$", "", head, flags=re.I).strip()
            items.append({"date": f"{m.group(3)}-{m.group(2)}-{m.group(1)}", "headline": head[:160],
                          "sensitive": "icon-price-sensitive" in tr or 'title="price sensitive"' in tr.lower(),  # every row has a pricesens cell; only sensitive ones hold the icon
                          "ids": link.group(2), "href": unescape(link.group(1))})
    return items


def pdf_text(item):
    url = "https://www.asx.com.au" + item["href"] if item["href"].startswith("/") else item["href"]
    body, final, ctype = get(url)
    calls["pdf"] += 1
    if "pdf" not in ctype:
        page = body.decode("utf-8", "replace")
        m = re.search(r'name="pdfURL"\s+value="([^"]+)"', page) or re.search(r'(https?://[^"\']+\.pdf)', page) \
            or re.search(r'(/asxpdf/[^"\']+\.pdf)', page)
        if not m:
            raise ValueError("no pdf link")
        u = m.group(1)
        if u.startswith("/"):
            u = "https://announcements.asx.com.au" + u
        body, final, ctype = get(u, 60)
        calls["pdf"] += 1
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(body))
    pages = reader.pages
    n = len(pages)
    clean = lambda t: re.sub(r"[ \t]+", " ", t).strip().encode("utf-8", "replace").decode("utf-8")
    # 2026-11-15 (cache raised to 100GB): keep every page, tagged with its number, so nothing in the middle is missed.
    return "\n".join(f"[page {i + 1}]\n" + clean(pages[i].extract_text() or "") for i in range(min(n, 400))), final, n

KEY = re.compile(r"\$\s?\d|A\$|million|\bMt\b|g/t|\bppm\b|% ?(Cu|Ni|Li2O|Zn|Pb|U3O8)|intercept|resource|reserve|grade|"
                 r"revenue|cash|placement|offtake|agreement|consideration|NPV|IRR|funding|debt|risk|trial|patient|"
                 r"endpoint|approval|contract|guidance", re.I)


def main():
    t0 = time.time()
    codes = []
    try:
        scan = json.loads(Path("out/study22_results.json").read_text(encoding="utf-8"))
        codes = [r["code"] for r in scan.get("rockets", [])]
    except Exception as e:
        print("no scan file", type(e).__name__)
    codes += [c.strip().upper() for c in os.environ.get("ANN_CODES", "CND").split(",") if c.strip()]
    codes = list(dict.fromkeys(codes))
    per = int(os.environ.get("ANN_PER_CODE", "12"))
    since = (date.today() - timedelta(days=30 * int(os.environ.get("ANN_MONTHS", "12")))).isoformat()
    years = sorted({int(since[:4]), date.today().year})
    out = {"generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "codes": codes, "items": {}}
    for c in codes:
        items = [i for i in listing(c, years) if i["date"] >= since]
        # price-sensitive first, then newest; always include the latest quarterly (Appendix 5B) for cash
        items.sort(key=lambda i: (not i["sensitive"], i["date"]), reverse=False)
        q = [i for i in items if re.search(r"quarterly|appendix 5b|4c", i["headline"], re.I)]
        pick = sorted(items, key=lambda i: (i["sensitive"], i["date"]), reverse=True)[:per]
        if q and q[0] not in pick:
            latest_q = max(q, key=lambda i: i["date"])
            if latest_q not in pick:
                pick = pick[:per - 1] + [latest_q]
        got = []
        for it in sorted(pick, key=lambda i: i["date"], reverse=True):
            try:
                text, url, pages = pdf_text(it)
                got.append({**{k: it[k] for k in ("date", "headline", "sensitive")}, "url": url, "pages": pages,
                            "text": text})
            except Exception as e:
                calls["failed"] += 1
                got.append({**{k: it[k] for k in ("date", "headline", "sensitive")}, "error": f"{type(e).__name__}: {e}"[:200]})
            time.sleep(0.4)
        out["items"][c] = {"listed": len(items), "fetched": got}
        print(c, "listed", len(items), "fetched", sum(1 for g in got if "text" in g), f"{time.time() - t0:.0f}s")
    out["calls"] = calls
    p = Path(os.environ.get("STUDY67_OUT", "out/study67_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
