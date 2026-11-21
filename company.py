"""公司简介 for one ASX stock (Frank 2026-11-21): who the company is, when it listed, who runs it, who owns it.

Runs on Frank's PC (the server), which can reach the ASX sites; nothing is committed.
  - ASX company API (markitdigital header / about / key-statistics): name, sector, industry, description, website,
    listing date, registry, address, directors and officers, shares on issue, market cap -- cached 7 days.
  - Top 20 shareholders: listing rule 4.10.9 makes every annual report print the 20 largest holders. We take the newest
    "Annual Report" (or a standalone "Top 20" announcement) of the last two years, read the whole PDF, find the top-20
    table and parse it by rule (no AI). Cached per announcement id, so each report is read once.
  - First trading day in our price history as a fallback for the listing date.
"""
import io
import json
import re
import time
import urllib.request
from datetime import date
from pathlib import Path

import study67

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "company"
API = "https://asx.api.markitdigital.com/asx-research/1.0/companies/{c}/{p}"


def _api(code, part):
    req = urllib.request.Request(API.format(c=code.lower(), p=part), headers={"Accept": "application/json",
                                 "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r).get("data") or {}


def asx_info(code):
    f = CACHE / f"{code}.info.json"
    if f.exists() and time.time() - f.stat().st_mtime < 7 * 86400:
        return json.loads(f.read_text(encoding="utf-8"))
    out = {}
    for part in ("header", "about", "key-statistics"):
        try:
            out[part] = _api(code, part)
        except Exception as e:
            out[part] = {"_error": type(e).__name__}
    if any(isinstance(v, dict) and v and "_error" not in v for v in out.values()):
        CACHE.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


MARK = re.compile(r"(twenty|20)\s+largest|top\s*(twenty|20)\b|largest\s+(twenty|20)\b", re.I)
ROW = re.compile(r"^\s*(?:\d{1,2}[.)]?\s+)?(.{3,90}?)\s+([\d]{1,3}(?:,\d{3})+|\d{5,})\s+(\d{1,2}\.\d{1,4})\s*%?\s*$")


def _pdf_pages(item):
    from annsvc import _pypdf
    url = "https://www.asx.com.au" + item["href"] if item["href"].startswith("/") else item["href"]
    body, final, ctype = study67.get(url, 60)
    if "pdf" not in ctype:
        page = body.decode("utf-8", "replace")
        m = re.search(r'name="pdfURL"\s+value="([^"]+)"', page) or re.search(r'(https?://[^"\']+\.pdf)', page)
        if not m:
            raise ValueError("no pdf")
        final = m.group(1)
        body, final, ctype = study67.get(final, 90)
    reader = _pypdf()(io.BytesIO(body))
    return [re.sub(r"[ \t]+", " ", p.extract_text() or "") for p in reader.pages], final


def parse_top20(pages):
    """Find the top-20 table and return rows [{name, shares, pct}] plus the page text it came from."""
    for k, p in enumerate(pages):
        if not MARK.search(p):
            continue
        txt = p[MARK.search(p).start():] + "\n" + (pages[k + 1] if k + 1 < len(pages) else "")
        rows, pend = [], ""
        for line in txt.splitlines():
            line = line.strip()
            if not line:
                continue
            m = ROW.match(line)
            if pend and (not m or len(m.group(1)) < 12):   # a wrapped name: "CITICORP NOMINEES PTY" / "LIMITED 1,234 5.6"
                m = ROW.match(pend + " " + line) or m
            if m:
                name = re.sub(r"\s+", " ", m.group(1)).strip(" .-")
                if re.match(r"(holder|name|rank|shareholder|registered|investor)", name, re.I):   # header words glued to row 1
                    cut = re.search(r"\s1[.)]?\s+(?=[A-Z])", name) or re.search(r"(?:%|\bIC\b|capital|units|holding|held)\s+(?=[A-Z])", name)
                    name = name[cut.end():] if cut else name
                if re.search(r"^(total|top 20|twenty)", name, re.I):
                    pend = ""
                    if rows:
                        break
                    continue
                rows.append({"name": name, "shares": int(m.group(2).replace(",", "")), "pct": float(m.group(3))})
                pend = ""
                if len(rows) >= 20:
                    break
            else:   # a long holder name wrapped onto the next line ("CITICORP NOMINEES PTY" / "LIMITED 1,234 5.6")
                pend = line if len(line) < 80 and not re.search(r"\d{1,3},\d{3}", line) else ""
        if len(rows) >= 5:
            return rows, k + 1
    return None, None


def top20(code):
    y = date.today().year
    items = study67.listing(code, [y, y - 1, y - 2])
    cands = [i for i in items if re.search(r"annual report|top\s*20|twenty largest|top twenty|shareholder information", i["headline"], re.I)
             and not re.search(r"notice of|letter|proxy|appendix 4g|corporate governance", i["headline"], re.I)]
    cands.sort(key=lambda i: i["date"], reverse=True)
    tried = []
    for it in cands[:4]:
        f = CACHE / f"top20v2_{it['ids']}.json"
        if f.exists():
            d = json.loads(f.read_text(encoding="utf-8"))
        else:
            try:
                pages, pdf = _pdf_pages(it)
                rows, page = parse_top20(pages)
                d = {"ids": it["ids"], "date": it["date"], "headline": it["headline"], "pdf": pdf, "rows": rows, "page": page}
            except Exception as e:
                d = {"ids": it["ids"], "date": it["date"], "headline": it["headline"], "rows": None, "error": type(e).__name__}
            CACHE.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tried.append(it["headline"])
        if d.get("rows"):
            d["top20_pct"] = round(sum(r["pct"] for r in d["rows"]), 2)
            return d
    return {"rows": None, "tried": tried}


def _pick(d, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def profile(code, first_day=None):
    info = asx_info(code)
    h, a, s = info.get("header") or {}, info.get("about") or {}, info.get("key-statistics") or {}
    people = _pick(a, "directors", "officers", "management", "people") or []
    out = {
        "code": code,
        "name": _pick(h, "displayName", "name") or _pick(a, "displayName", "name"),
        "sector": _pick(h, "sector") or _pick(a, "sector"),
        "industry": _pick(h, "industryGroup", "industry") or _pick(a, "industry"),
        "description": _pick(a, "description", "principalActivities") or _pick(h, "description"),
        "website": _pick(a, "websiteUrl", "website") or _pick(h, "website"),
        "listed": _pick(a, "dateListed", "listingDate", "listedDate") or _pick(h, "dateListed", "listingDate"),
        "first_trade": first_day,
        "address": _pick(a, "addressContact", "address", "registeredOffice"),
        "phone": _pick(a, "phoneNumber", "phone"),
        "registry": _pick(a, "addressShareRegistry", "registry", "shareRegistry"),
        "secretaries": _pick(a, "secretaries") or [],
        "auditor": _pick(a, "auditor"),
        "people": people,
        "shares": _pick(s, "numOfShares", "sharesOnIssue", "sharesIssued") or _pick(h, "numOfShares"),
        "mcap": _pick(h, "marketCap") or _pick(s, "marketCap"),
        "price": _pick(h, "priceLast"),
        "isin": _pick(s, "isin"),
        "assets": _pick(s, "totalAssets"), "liabilities": _pick(s, "totalLiabilities"),
        "raw_keys": {k: sorted(v.keys()) if isinstance(v, dict) else None for k, v in info.items()},
    }
    try:
        out["top20"] = top20(code)
    except Exception as e:
        out["top20"] = {"rows": None, "error": type(e).__name__}
    return out
