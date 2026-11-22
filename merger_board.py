"""Round 83, part 1 (Frank 2026-11-22): the live merger-arbitrage board. The backtest half comes later.

RULES (written before any code ran; change them only with a dated note here)
---------------------------------------------------------------------------
What is listed
  * Every ASX target with a binding deal in flight: a scheme (implementation deed/agreement signed, booklet, scheme
    meeting) or a takeover bid (bidder's / target's statement, bid implementation agreement, unconditional notice),
    first announced within the last 18 months, whose shares still traded within the last 7 calendar days, and with no
    later announcement that the deal ended (terminated / lapsed / withdrawn / voted down / effective / implemented /
    compulsory acquisition). Non-binding proposals (NBIOs) alone are not listed.
  * The page is the TARGET's: a code counts as a target only if it has a target-only headline (booklet, notice of
    scheme meeting, target's statement, court hearing, independent expert) or its deal document says the board
    recommends shareholders vote for / accept "in the absence of a superior proposal" (acquirers never say that).
Where deals are found (all free)
  1. Our announcement archive (data/ann, ~1,500 small caps): headlines of the last 18 months.
  2. The market-wide ASX announcements feed (asx.api.markitdigital.com): back-filled once as far as it pages, then the
     daily round-22 scan adds every new deal headline to a log kept in the Actions cache (data/merger/feed.json).
  3. Price signal over every ASX stock we price (round 37 method, any size): a >= 10% jump on >= 3x volume in the last
     18 months, then a pinned price (median absolute daily move over the last 40 sessions < 1%). Those codes' yearly
     ASX announcement lists are fetched to confirm (a few hundred requests, spaced, with round 67's brake).
  4. Cross-checks: the ACCC acquisitions register (mandatory notification regime) -- targets matched to listed
     company names; the Takeovers Panel applications page. Matches not found by 1-3 are added and flagged.
What is extracted (code first; whatever code cannot find is "待核" and can be filled by hand, see below)
  offer (cash / scrip ratio + acquirer code / mixed), special dividend and franking, conditions (FIRB, ACCC, court,
  shareholder vote, minimum acceptance, independent expert conclusion and valuation range), break fee, key dates
  (scheme meeting, second court hearing, implementation date, offer close), ATO class ruling number, ACCC case,
  stage and status. Every number carries its source: announcement headline, date and link. Later documents override
  earlier ones (revised offers). Hand-read facts go in research/merger_facts.json (kept encrypted in the repo as
  merger_facts.json.enc, Claude reads key pages in a session with subscription usage, no API) and override code.
Maths
  * price = last raw close (EODHD daily). Scrip: offer value = ratio x acquirer's last raw close (unhedged).
  * spread = offer value (+ special dividend if paid on top) - price; gross return = spread / price.
  * expected completion: the extracted implementation date / offer close date; if none, announcement + 141 days for
    schemes, + 82 days for takeovers (HSF Australian Public M&A Report 2026, FY26 averages) and marked "估计".
  * annualised = (1 + return) ^ (365 / days left) - 1; days left floored at 7.
  * after tax, three rates 0% / 32.5% / 47%; holding < 12 months so no CGT discount. A special dividend paid out of
    the consideration lowers the capital proceeds (proceeds = offer - dividend). Franked dividend: net = (D + F)(1 - t)
    where F = D x franking% x 30/70 (0% rate gets F back as a refund). Franking needs the 45-day holding rule (ex-date
    at least 46 days after purchase), unless total franking credits for the year are under A$5,000 (small
    shareholder exemption) -- both shown. A capital loss is assumed usable against other capital gains (noted).
    Scrip: rollover relief may defer the gain if the acquirer ends with >= 80% (noted, not computed). Class ruling
    number shown when the documents name one.
  * market-implied success = (price - fail price) / (offer value - fail price); fail price = 20-session average raw
    close before the first deal headline x (small ordinaries index now / at that date). Clamped to 0-100%.
Risk tags: ACCC phase 2; independent expert "not fair"; a substantial holder says it will vote against / not accept;
  annualised spread > 25% (then the board says what the market seems to worry about: phase 2, expert, opposer,
  scrip, FIRB, long timetable, or "原因不明，待核").
Alerts (ntfy, only if the NTFY_TOPIC secret is set): a new deal; spread moved > 2 percentage points in a day; an ACCC
  decision; a court hearing / scheme meeting / offer close within 3 days.
Never: orders, IBKR, paid data. Output out/merger_board.json (encrypted by the workflow, never public in plain text).
"""
import bisect
import gzip
import json
import os
import re
import statistics
import time
import urllib.request
from datetime import date, timedelta
from html import unescape
from pathlib import Path

import study

MDIR = study.DATA / "merger"
OUT = Path(os.environ.get("MERGER_OUT", "out/merger_board.json"))
TODAY = date.today()
SINCE = (TODAY - timedelta(days=548)).isoformat()
SCHEME_DAYS, TAKEOVER_DAYS = 141, 82
RATES = (0.0, 0.325, 0.47)

DEAL = re.compile(r"scheme implementation|implementation (deed|agreement)|scheme booklet|notice of scheme meeting|scheme meeting|"
                  r"first court|second court|bidder'?s statement|target'?s statement|takeover bid|takeover offer|"
                  r"off-market (takeover|bid)|on-market (takeover|bid)|binding scheme|"
                  r"supplementary (bidder|target)|offer (increased|improved)|increased offer|improved offer|"
                  r"best and final|recommended (cash )?(offer|takeover|scheme)", re.I)
TARGET_ONLY = re.compile(r"scheme booklet|notice of scheme meeting|scheme meeting|first court|second court|target'?s statement|"
                         r"results? of scheme meeting|court approv", re.I)
NOT_DEAL = re.compile(r"demerger|de-merger|in-specie|capital reduction", re.I)
ENDED = re.compile(r"\bterminat|\blapse|withdraw|not proceed|scheme not approved|not approved by|did not pass|resolutions? not passed|"
                   r"scheme effective|scheme becomes effective|effective date|implementation of (the )?scheme|scheme (has been )?implemented|"
                   r"completion of (the )?(scheme|acquisition|takeover|merger|offer)|(scheme|takeover|merger|acquisition) complet|"
                   r"compulsory acquisition|removal from (the )?official list|delist|close of (the )?offer|offer (period )?(has )?(closed|ended)|"
                   r"suspension of trading.{0,30}scheme", re.I)
DEAL_CTX = re.compile(r"scheme|offer|\bbid\b|bidder|deed|implementation agreement|takeover|transaction|official list|compulsory|delist", re.I)
TARGET_VOICE = re.compile(r"in the absence of a superior proposal", re.I)
MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                          "september", "october", "november", "december"])}
DATE = r"(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|November|December),?\s+(20\d\d)"


def ann_url(ids):
    return f"https://www.asx.com.au/asx/v2/statistics/displayAnnouncement.do?display=pdf&idsId={ids}"


def pdate(m):
    try:
        return date(int(m[2]), MONTHS[m[1].lower()], int(m[0])).isoformat()
    except (ValueError, KeyError):
        return None


def jload(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def jsave(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def is_deal(h):
    if NOT_DEAL.search(h or ""):
        return False
    if DEAL.search(h or ""):
        return True
    return bool(re.search(r"independent expert|unconditional", h or "", re.I) and re.search(r"scheme|offer|\bbid\b|takeover", h or "", re.I))


# ------------------------------------------------------------------ extraction (pure functions, tested offline)
def _money(a, b):
    """('1.20', None) dollars or (None, '22.5') cents -> dollars."""
    return float(a) if a else float(b) / 100 if b else None


def extract(text):
    """Return {field: value} found in one document's text. Missing fields are simply absent."""
    t = re.sub(r"\s+", " ", text or "")
    out = {}
    cash = []
    for m in re.finditer(r"(?:A\$|\$)\s?(\d+(?:\.\d+)?)\s*(?:in )?(?:cash\s+)?(?:per|for each|for every)\s+(?:\w+\s+){0,2}(?:share|unit|security)"
                         r"|(\d+(?:\.\d+)?)\s*cents?\s*(?:in )?(?:cash\s+)?(?:per|for each)\s+(?:\w+\s+){0,2}(?:share|unit|security)", t, re.I):
        v = _money(m.group(1), m.group(2))
        ctx = t[max(0, m.start() - 160):m.start()].lower()
        if v and 0 < v < 1000 and not re.search(r"dividend|exercise|strike|option|placement|issue price|vwap|last close|closing price", ctx[-60:]):
            w = 3 if re.search(r"scheme consideration|offer price|offer consideration|cash consideration|consideration of|will receive|to acquire", ctx) else 1
            cur = "USD" if t[max(0, m.start() - 2):m.start()] == "US" else "NZD" if t[max(0, m.start() - 2):m.start()] == "NZ" else "AUD"
            cash += [(round(v, 6), cur)] * w
    if cash:
        out["cash"], cur = max(set(cash), key=cash.count)
        if cur != "AUD":
            out["currency"] = cur
    m = re.search(r"(\d+(?:\.\d+)?)\s+(?:new\s+|fully paid\s+)?(?:[A-Z][\w&'.]*\s+){0,3}(?:ordinary\s+)?shares?\s+(?:for\s+(?:every|each)|per)\s+"
                  r"(?:(\d+(?:\.\d+)?)\s+)?(?:[A-Z][\w&'.]*\s+){0,3}(?:ordinary\s+)?shares?", t)
    if m and re.search(r"consideration|offer|scheme|receive|acquire", t[max(0, m.start() - 200):m.start()], re.I):
        a, b = float(m.group(1)), float(m.group(2) or 1)
        if a > 0 and b > 0:
            out["scrip_ratio"] = round(a / b, 8)
    m = re.search(r"special dividend of (?:up to |approximately )?(?:(?:A\$|\$)\s?(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*cents)", t, re.I)
    if m:
        out["special_div"] = _money(m.group(1), m.group(2))
        near = t[max(0, m.start() - 40):m.start() + 300].lower()
        out["franking"] = 1.0 if "fully franked" in near else 0.0 if "unfranked" in near else None
        out["div_on_top"] = bool(re.search(r"in addition to the (scheme|offer) consideration", near))
    m = re.search(r"independent expert[^.]{0,200}?conclu\w+[^.]{0,120}?(not fair (?:but|and) (?:not )?reasonable|fair and reasonable)", t, re.I)
    if m:
        out["ie"] = m.group(1).lower()
    m = re.search(r"(?:assessed|valued|valuation|value)[^.]{0,160}?(?:A\$|\$)\s?(\d+\.\d+)\s*(?:to|and|-|–)\s*(?:A\$|\$)\s?(\d+\.\d+)\s*per", t, re.I) or \
        re.search(r"(?:assessed|valued|valuation|value)[^.]{0,160}?(\d+(?:\.\d+)?)\s*(?:to|and|-|–)\s*(\d+(?:\.\d+)?)\s*cents per", t, re.I)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if "cents" in m.group(0).lower():
            lo, hi = lo / 100, hi / 100
        if 0 < lo <= hi:
            out["ie_range"] = [lo, hi]
    m = re.search(r"(?:break|termination|reimbursement) fee of (?:approximately |up to )?(?:A\$|\$)\s?([\d,.]+)\s*(million|m\b)?", t, re.I)
    if m:
        v = float(m.group(1).replace(",", "") or 0)
        out["break_fee"] = v * (1e6 if m.group(2) else 1)
    for key, lab in (("meeting", r"scheme meeting"), ("court2", r"second court (?:date|hearing)"),
                     ("implementation", r"implementation date"), ("offer_close", r"offer (?:period )?(?:is scheduled to )?clos\w*|close of the offer")):
        hits = [pdate((m.group(1), m.group(2), m.group(3))) for m in re.finditer("(?:" + lab + r")[^.]{0,90}?" + DATE, t, re.I)
                if not re.search(r"end date|sunset|later than|on or before|by no later|prior to|before the", m.group(0), re.I)]
        hits = [h for h in hits if h]
        if hits:
            out[key] = hits[0]   # the timetable's own date, not a sunset / end date further on
    conds = []
    for w, lab in ((r"\bFIRB\b|Foreign Investment Review Board", "FIRB"), (r"\bACCC\b", "ACCC"), (r"\bcourt\b", "法院"),
                   (r"scheme meeting|shareholder approval", "股东投票"), (r"independent expert", "独立专家")):
        if re.search(w, t, re.I):
            conds.append(lab)
    m = re.search(r"minimum acceptance condition of (\d+(?:\.\d+)?)\s*%", t, re.I)
    if m:
        conds.append(f"最低接受 {m.group(1)}%")
    if conds:
        out["conditions"] = conds
    m = re.search(r"Class Ruling\s*(CR\s?\d{4}/\d+)", t)
    if m:
        out["class_ruling"] = m.group(1).replace(" ", "")
    elif re.search(r"(appl\w+ for|request\w*) a class ruling", t, re.I):
        out["class_ruling"] = "已申请（未出）"
    if re.search(r"(intends? to|will) (vote against|not accept|reject)|opposes? the (scheme|offer)", t, re.I):
        out["opposer"] = True
    codes = re.findall(r"\((?:ASX|ASX code|ASX:)\s*:?\s*([A-Z0-9]{3})\)", t)
    if codes:
        out["asx_codes"] = list(dict.fromkeys(codes))
    if TARGET_VOICE.search(t):
        out["target_voice"] = True
    return out


# ------------------------------------------------------------------ maths (pure)
def annualise(ret, days):
    days = max(days, 7)
    if ret <= -1:
        return -1.0
    return (1 + ret) ** (365 / days) - 1


def after_tax(price, offer, div, frank, rate, div_on_top=False, frank_ok=True):
    """Net return per $1 invested at one marginal rate. Holding < 12 months: no CGT discount."""
    div = div or 0.0
    f = div * (frank or 0) * 30 / 70 if frank_ok else 0.0
    proceeds = offer if div_on_top else offer - div
    cap = (proceeds - price) * (1 - rate)
    inc = (div + f) * (1 - rate) if frank_ok else div * (1 - rate)
    return (cap + inc) / price


def implied(price, offer, fail):
    if not fail or offer is None or offer <= fail:
        return None
    return max(0.0, min(1.0, (price - fail) / (offer - fail)))


def why_wide(d):
    w = []
    if d.get("accc_stage") == "phase_2":
        w.append("ACCC 第二阶段审查")
    if (d.get("ie") or "").startswith("not fair"):
        w.append("独立专家说不公平")
    if d.get("opposer"):
        w.append("有大股东反对")
    if d.get("scrip_ratio"):
        w.append("换股，收购方股价会动")
    if "FIRB" in (d.get("conditions") or []):
        w.append("要 FIRB 批准")
    if d.get("days_left", 0) > 200:
        w.append("时间表很长")
    return w or ["原因不明，待核"]


# ------------------------------------------------------------------ data sources (run on GitHub Actions)
def _get(url, timeout=40, accept="text/html,application/json"):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def archive_hits():
    """Deal headlines in our own announcement archive (small caps), last 18 months."""
    hits = {}
    for f in (study.DATA / "ann").glob("*.json.gz"):
        try:
            d = json.loads(gzip.decompress(f.read_bytes()))
        except Exception:
            continue
        code = f.name[:-8]
        for it in d.get("listed", []):
            if it.get("date", "") >= SINCE and DEAL.search(it.get("headline", "")):
                hits.setdefault(code, []).append(it)
        if code in hits:
            hits[code] = {"items": d.get("listed", []), "docs": d.get("docs", {})}
    return hits


FEED = "https://asx.api.markitdigital.com/asx-research/1.0/markets/announcements?page={p}&itemsPerPage=100"


def feed_log():
    """Market-wide deal headlines: back-fill once as far as the feed pages, then add the daily round-22 catch."""
    p = MDIR / "feed.json"
    log = jload(p, {"items": {}, "backfilled": False})
    if not log["backfilled"]:
        oldest, pages = None, 0
        for page in range(0, 2500):
            try:
                items = json.loads(_get(FEED.format(p=page), 30, "application/json"))["data"]["items"]
            except Exception as e:
                print("[merger] feed stops at page", page, type(e).__name__)
                break
            pages += 1
            if not items:
                break
            for it in items:
                h = it.get("headline") or ""
                if DEAL.search(h + " " + " ".join(it.get("announcementTypes") or [])):
                    k = f"{it.get('symbol')}|{it.get('date', '')[:10]}|{h}"
                    log["items"][k] = {"code": it.get("symbol"), "date": it.get("date", "")[:10], "headline": h,
                                       "ids": str(it.get("documentKey") or it.get("id") or "")}
            oldest = items[-1].get("date", "")[:10]
            if oldest < SINCE:
                break
            time.sleep(0.25)
        log["backfilled"] = True
        log["backfill_note"] = f"{pages} pages, oldest {oldest}"
        print("[merger] feed back-fill:", log["backfill_note"], "deal headlines", len(log["items"]))
    scan = jload("out/study22_results.json", {})
    for it in (scan.get("announcements") or {}).get("收购/协议安排", []):
        k = f"{it.get('code')}|{(it.get('time') or '')[:10]}|{it.get('headline')}"
        log["items"].setdefault(k, {"code": it.get("code"), "date": (it.get("time") or "")[:10], "headline": it.get("headline"), "ids": ""})
    log["items"] = {k: v for k, v in log["items"].items() if v["date"] >= SINCE}
    jsave(p, log)
    return {v["code"] for v in log["items"].values() if v.get("code")}, log.get("backfill_note", "")


def price_candidates(live):
    """Round 37 signal over every priced stock: jump >= 10% on >= 3x volume in 18 months, then a pinned price."""
    out = set()
    for code in live:
        rows = study.load(f"{code}.AU")
        if len(rows) < 80:
            continue
        ds = [r[0] for r in rows]
        i0 = bisect.bisect_left(ds, SINCE)
        jumped = False
        for i in range(max(i0, 21), len(rows)):
            pc = rows[i - 1][6]
            vols = sorted(r[5] for r in rows[i - 20:i])
            if pc > 0 and vols[10] > 0 and rows[i][6] / pc - 1 >= .10 and rows[i][5] >= 3 * vols[10]:
                jumped = True
                break
        if not jumped or statistics.median(r[6] * r[5] for r in rows[-20:]) < 20_000:
            continue
        moves = [abs(rows[k][6] / rows[k - 1][6] - 1) for k in range(len(rows) - 40, len(rows)) if rows[k - 1][6] > 0]
        if moves and statistics.median(moves) < 0.01:
            out.add(code)
    return out


def liquid(live):
    out = []
    for code in live:
        rows = study.load(f"{code}.AU")
        if len(rows) >= 20 and statistics.median(r[6] * r[5] for r in rows[-20:]) >= 20_000:
            out.append(code)
    return out


def norm(name):
    n = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    n = re.sub(r"\b(limited|ltd|pty|holdings?|group|the|plc|inc|corporation|corp|nl|trust|fund|reit)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


ACCC_DEBUG = {}


def accc_register(names):
    """ACCC acquisitions register: entries under assessment or decided in 18 months, matched to listed names."""
    base = "https://www.accc.gov.au/public-registers/acquisitions-and-mergers-registers/acquisitions-register"
    found = {}
    by_name = {norm(n): c for c, n in names.items() if norm(n)}
    for page in range(0, 45):
        try:
            html = _get(f"{base}?page={page}", 40)
        except Exception as e:
            print("[merger] ACCC page", page, type(e).__name__)
            break
        links = re.findall(r'href="(/public-registers/acquisitions-and-mergers-registers/acquisitions-register/[^"?#]+)"', html)
        body = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        page_txt = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", body)))
        if page == 0:
            k0 = max(0, page_txt.find("Case number") - 600)
            ACCC_DEBUG["page0"] = page_txt[k0:k0 + 1500]
            ACCC_DEBUG["links"] = len(links)
        parts = re.split(r"(?=Acquisition status)", page_txt)
        n_cards = 0
        for k, txt in enumerate(parts):
            m = re.search(r"Case number:?\s*([A-Z]{2}-\d+)", txt)
            if not m:
                continue
            n_cards += 1
            stage = "phase_2" if re.search(r"phase 2|detailed assessment", txt, re.I) else "public_benefit" if re.search(r"public benefit", txt, re.I) \
                else "phase_1" if re.search(r"phase 1|initial assessment", txt, re.I) else "waiver" if re.search(r"waiver", txt, re.I) else None
            status = "under_assessment" if re.search(r"under assessment", txt, re.I) else "approved_with_conditions" if re.search(r"approved with conditions", txt, re.I) \
                else "not_approved" if re.search(r"not approved", txt, re.I) else "approved" if re.search(r"\bapproved\b", txt, re.I) \
                else "completed" if re.search(r"assessment completed", txt, re.I) else None
            tgt = re.search(r"Target\(s\)(?: or vendor\(s\))?:?\s*(.+?)\s*(?:Other part|ANZSIC|Description|Read more|$)", txt)
            title = parts[k - 1][-160:] if k else ""
            for chunk in re.split(r";|,(?= [A-Z])", tgt.group(1) if tgt else ""):
                code = by_name.get(norm(chunk))
                if code:
                    found[code] = {"case": m.group(1), "stage": stage, "status": status, "title": (title.strip() or txt[:120])[-140:],
                                   "url": base + "?search_api_fulltext=" + m.group(1)}
        if n_cards == 0:
            break
        time.sleep(0.5)
    return found


def panel_names():
    """Takeovers Panel: company names in the applications list (best effort; the page layout may change)."""
    names = []
    for page in range(0, 4):
        try:
            html = _get(f"https://takeovers.gov.au/media-releases?page={page}", 40)
        except Exception as e:
            print("[merger] panel page", page, type(e).__name__)
            break
        txt = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))
        for d_, n_ in re.findall(r"(\d{2}/\d{2}/20\d\d)\s*\|?\s*([A-Z][\w&'.()-]*(?: [A-Z&][\w&'.()-]*){0,6} (?:Limited|Ltd))", txt):
            iso = f"{d_[6:]}-{d_[3:5]}-{d_[:2]}"
            if iso >= SINCE:
                names.append(n_)
        time.sleep(0.5)
    return list(dict.fromkeys(names))


def listings(code, max_age=20 * 3600):
    """Yearly ASX announcement lists (this year + last), cached (20 hours for deal candidates, 6 days for the sweep)."""
    import study67
    p = MDIR / "lists" / f"{code}.json"
    hit = jload(p, None)
    if hit and hit.get("items") and time.time() - hit.get("t", 0) < max_age:
        return hit["items"]
    items = study67.listing(code, [TODAY.year - 1, TODAY.year])
    time.sleep(0.3)
    if items:   # never cache an empty answer (ASX hiccup) -- it would hide a deal for days
        jsave(p, {"t": time.time(), "items": items})
        return items
    return (hit or {}).get("items", [])


def doc_text(code, it, archive):
    """Full text of one announcement: our archive first, then the deal cache, then ASX (cached forever)."""
    d = (archive.get(code) or {}).get("docs", {}).get(it["ids"])
    if d and d.get("text"):
        return d["text"]
    p = MDIR / "docs" / f"{it['ids']}.txt.gz"
    if p.exists():
        return gzip.decompress(p.read_bytes()).decode("utf-8", "replace")
    import study67
    try:
        if not it.get("href"):
            it = dict(it, href=f"/asx/v2/statistics/displayAnnouncement.do?display=pdf&idsId={it['ids']}")
        text = study67.pdf_text(it)[0]
    except Exception as e:
        print("[merger] pdf failed", code, it["ids"], type(e).__name__)
        return ""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(gzip.compress(text.encode("utf-8")))
    return text


# ------------------------------------------------------------------ assemble one deal
def other_prefix(headline, code):
    """'HZN: Horizon takeover offer' on CUE's page = a document lodged by HZN, not by CUE."""
    m = re.match(r"\s*([A-Z0-9]{3})\s*:", headline or "")
    return bool(m and m.group(1) != code)



def deal_for(code, items, archive, names):
    items = sorted([i for i in items if i.get("date", "") >= SINCE], key=lambda i: (i["date"], i["ids"]))
    ends = [i for i in items if ENDED.search(i["headline"]) and DEAL_CTX.search(i["headline"])
            and not re.search(r"scheme meeting|booklet|timetable|extension|extended|variation", i["headline"], re.I)]
    last_end = ends[-1]["date"] if ends else ""
    word = re.escape((norm(names.get(code)) or "?").split(" ")[0])
    named = re.compile(r"(acquire|acquisition of|offer for|bid for|takeover of|merge with|merger with)\s+(the\s+)?" + word, re.I)
    deal_items = [i for i in items if (is_deal(i["headline"]) or named.search(i["headline"])) and i["date"] > last_end]   # current episode
    if named.pattern and any(named.search(i["headline"]) for i in deal_items):
        named_target = True
    else:
        named_target = False
    if not deal_items:
        return None, (f"ended {last_end}: {ends[-1]['headline'][:60]}" if ends else "no deal headline")
    start = deal_items[0]["date"]
    if start < (TODAY - timedelta(days=455)).isoformat() and deal_items[-1]["date"] < (TODAY - timedelta(days=120)).isoformat():
        return None, f"stale: started {start}, nothing since {deal_items[-1]['date']}"
    facts, src = {}, {}
    own = [i for i in deal_items if not other_prefix(i["headline"], code)]   # documents this company lodged itself
    target = named_target or any(TARGET_ONLY.search(i["headline"]) or re.search(r"(offer|bid|proposal) (for|to acquire) " + re.escape((names.get(code) or "?").split()[0]), i["headline"], re.I)
                 for i in own)
    for it in deal_items[-12:] if len(deal_items) > 12 else deal_items:
        ex = extract(doc_text(code, it, archive))
        if ex.get("target_voice") and not other_prefix(it["headline"], code):
            target = True
        for k, v in ex.items():
            if k in ("target_voice",):
                continue
            facts[k] = v          # later documents override earlier ones (revised offers)
            src[k] = {"headline": it["headline"], "date": it["date"], "url": ann_url(it["ids"])}
    if not target:
        return None, "acquirer side or not confirmed as target"
    acq = [c for c in facts.get("asx_codes", []) if c != code]
    d = {"code": code, "name": names.get(code, ""), "kind": "scheme" if any(re.search(r"scheme", i["headline"], re.I) for i in deal_items) else "takeover",
         "role_by": "headline" if any(TARGET_ONLY.search(i["headline"]) for i in own) else "document wording",
         "announced": start, "first_headline": deal_items[0]["headline"], "first_url": ann_url(deal_items[0]["ids"]),
         "latest": [{"date": i["date"], "headline": i["headline"], "url": ann_url(i["ids"])} for i in deal_items[-6:]][::-1],
         "acquirer_code": acq[0] if acq else None, "src": src}
    d.update({k: v for k, v in facts.items() if k != "asx_codes"})
    return d, "ok"


def price_deal(d, idx):
    rows = study.load(f"{d['code']}.AU")
    if not rows:
        d["pending"] = "没有价格"
        return d
    d["price"], d["price_date"] = rows[-1][6], rows[-1][0]
    offer = d.get("cash") or 0.0
    if offer and d.get("currency") in ("USD", "NZD"):
        fx = fx_rate(d["currency"])
        if fx:
            d["fx"] = {"pair": f"AUD{d['currency'][:2]}", "rate": fx[1], "date": fx[0]}
            offer = offer / fx[1]
        else:
            d["pending"] = f"报价是 {d['currency']}，汇率没拿到，待核"
            return d
    if d.get("scrip_ratio") and d.get("acquirer_code"):
        arows = study.load(f"{d['acquirer_code']}.AU")
        if arows:
            offer += d["scrip_ratio"] * arows[-1][6]
            d["acquirer_px"], d["acquirer_px_date"] = arows[-1][6], arows[-1][0]
    elif d.get("scrip_ratio"):
        d["pending"] = "换股比例有了，收购方代码待核"
    if offer <= 0:
        d["pending"] = d.get("pending") or "报价待核"
        return d
    d["offer_value"] = round(offer, 6)
    div, on_top = d.get("special_div") or 0.0, d.get("div_on_top")
    gross = offer + (div if on_top else 0.0)
    end = d.get("implementation") or d.get("offer_close")
    if end and end >= TODAY.isoformat():
        d["end"], d["end_basis"] = end, "公告"
    else:
        dd = date.fromisoformat(d["announced"]) + timedelta(days=SCHEME_DAYS if d["kind"] == "scheme" else TAKEOVER_DAYS)
        if dd <= TODAY:
            dd = TODAY + timedelta(days=30)
            d["end_basis"] = "待核（已过常见完成期，先按 30 天，不算年化）"
            d["no_ann"] = True
        else:
            d["end_basis"] = f"估计（宣布 + {SCHEME_DAYS if d['kind'] == 'scheme' else TAKEOVER_DAYS} 天，HSF FY26 平均）"
        d["end"] = dd.isoformat()
    days = max((date.fromisoformat(d["end"]) - TODAY).days, 7)
    d["days_left"] = days
    ret = gross / d["price"] - 1
    d["spread"], d["spread_pct"] = round(gross - d["price"], 6), round(100 * ret, 2)
    if not -0.10 <= ret <= 0.50 and not d.get("manual"):
        d["pending"] = f"价差 {100 * ret:.0f}% 不合理：报价提取错、看反了收购方，或交易已变，待核"
        return d
    d["ann_pct"] = None if d.get("no_ann") else round(100 * annualise(ret, days), 1)
    if (d["ann_pct"] or 0) > 100 and not d.get("manual"):
        d["pending"] = "自动提取，年化超过 100%，多半是报价或日期读错，待核（不参与排名）"
    frank = d.get("franking") or 0.0
    ok45 = days >= 46          # bought today, ex-date assumed near completion
    d["after_tax"] = {}
    for r in RATES:
        net = after_tax(d["price"], gross, div, frank, r, bool(on_top), frank_ok=ok45)
        net_small = after_tax(d["price"], gross, div, frank, r, bool(on_top), frank_ok=True)
        d["after_tax"][f"{r:.3f}"] = {"ret_pct": round(100 * net, 2), "ann_pct": None if d.get("no_ann") else round(100 * annualise(net, days), 1),
                                      "ann_small_holder_pct": None if d.get("no_ann") else round(100 * annualise(net_small, days), 1)}
    d["tax_notes"] = ["持有不满 12 个月，资本利得没有折扣"]
    if div:
        d["tax_notes"].append(f"特别股息 {div:g}，{'全额' if frank == 1 else '无' if frank == 0 else '待核'}免税额度；"
                              + ("45 天持有规则够" if ok45 else "离完成不到 46 天，45 天规则可能不够（全年免税额度 5,000 澳元以下的小股东不受限）"))
    if d.get("scrip_ratio"):
        d["tax_notes"].append("换股：收购方最终持股 ≥80% 时通常可以选换股递延（rollover），待核")
    if d.get("class_ruling"):
        d["tax_notes"].append(f"ATO 类别裁定：{d['class_ruling']}")
    d["tax_notes"].append("资本亏损按能抵其他资本利得算")
    # market-implied success
    ds = [r[0] for r in rows]
    i = bisect.bisect_left(ds, d["announced"])
    pre = [r[6] for r in rows[max(0, i - 20):i]]
    if len(pre) >= 10 and idx:
        ixd = [r[0] for r in idx]
        j = max(0, bisect.bisect_left(ixd, d["announced"]) - 1)
        fail = statistics.mean(pre) * idx[-1][4] / idx[j][4]
        d["fail_px"] = round(fail, 6)
        p = implied(d["price"], gross, fail)
        d["implied_pct"] = round(100 * p, 1) if p is not None else None
    tags = []
    if d.get("accc_stage") == "phase_2":
        tags.append("ACCC 第二阶段")
    if (d.get("ie") or "").startswith("not fair"):
        tags.append("专家：不公平")
    if d.get("opposer"):
        tags.append("有反对的大股东（待核）")
    if (d["ann_pct"] or 0) > 25:
        tags.append("年化 >25%：市场在担心 " + "、".join(why_wide(d)))
    d["risk"] = tags
    return d


_FX = {}


def fx_rate(cur):
    """AUD per 1 unit of quote currency via EODHD (AUDUSD = USD per AUD). Returns (date, USD-or-NZD per AUD)."""
    if cur not in _FX:
        try:
            rows = study.api(f"eod/AUD{cur[:2]}D.FOREX", **{"from": (TODAY - timedelta(days=10)).isoformat()})
            _FX[cur] = (rows[-1]["date"], float(rows[-1]["close"])) if rows else None
        except Exception:
            _FX[cur] = None
    return _FX[cur]


def next_key_date(d):
    for k, lab in (("meeting", "股东大会"), ("court2", "第二次法院听证"), ("offer_close", "要约截止"), ("implementation", "实施")):
        v = d.get(k)
        if v and v >= TODAY.isoformat():
            return v, lab
    return None, None


# ------------------------------------------------------------------ alerts
def ntfy(title, msg):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False
    import base64
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=msg.encode("utf-8"), method="POST",
                                 headers={"Title": "=?UTF-8?B?" + base64.b64encode(title.encode()).decode() + "?=", "Tags": "handshake"})
    try:
        with urllib.request.urlopen(req, timeout=30):
            return True
    except Exception as e:
        print("[merger] ntfy failed", type(e).__name__)
        return False


def alerts(prev, deals):
    old = {d["code"]: d for d in (prev or {}).get("deals", [])}
    msgs = []
    for d in deals:
        o = old.get(d["code"])
        if not o and prev:
            msgs.append(f"新交易 {d['code']}：{d['kind']}，报价 {d.get('offer_value', '待核')}，价差 {d.get('spread_pct', '待核')}%")
        if o and d.get("spread_pct") is not None and o.get("spread_pct") is not None and abs(d["spread_pct"] - o["spread_pct"]) > 2:
            msgs.append(f"{d['code']} 价差 {o['spread_pct']}% → {d['spread_pct']}%")
        if o and d.get("accc_status") and d.get("accc_status") != o.get("accc_status") and d["accc_status"] != "under_assessment":
            msgs.append(f"{d['code']} ACCC 出决定：{d['accc_status']}")
        k, lab = d.get("next_date"), d.get("next_label")
        if k and 0 <= (date.fromisoformat(k) - TODAY).days <= 3 and not (o and o.get("next_date") == k and o.get("alerted_near")):
            msgs.append(f"{d['code']} {lab} {k}")
            d["alerted_near"] = True
        elif o and o.get("next_date") == k:
            d["alerted_near"] = o.get("alerted_near", False)
    for d in (prev or {}).get("deals", []):
        if d["code"] not in {x["code"] for x in deals}:
            msgs.append(f"{d['code']} 不在在途名单了（完成、终止或停牌），去看公告")
    return msgs


# ------------------------------------------------------------------ main
def load_manual():
    """Hand-read facts: research/merger_facts.json (local, git-ignored) or its encrypted copy in the repo."""
    plain = Path("research/merger_facts.json")
    if plain.exists():
        return jload(plain, {})
    enc = Path("research/merger_facts.json.enc")
    if enc.exists() and os.environ.get("ASX_DATA_KEY"):
        try:
            import securedata
            return json.loads(securedata.decrypt(enc.read_bytes()))
        except Exception as e:
            print("[merger] manual facts unreadable", type(e).__name__)
    return {}


def main():
    t0 = time.time()
    MDIR.mkdir(parents=True, exist_ok=True)
    prev = jload(OUT, None)
    sym = study.api("exchange-symbol-list/AU") or []
    names = {r["Code"]: r.get("Name", "") for r in sym if r.get("Code")}
    idx = study.load("AXSO.INDX")
    last_day = max((r[0] for r in idx), default=TODAY.isoformat())
    live = [f.name[:-8] for f in study.CACHE.glob("*.AU.json") if (lambda r: r and r[-1][0] >= (TODAY - timedelta(days=7)).isoformat())(json.loads(f.read_text() or "[]"))]
    archive = archive_hits()
    feed_codes, feed_note = feed_log()
    px = price_candidates(live)
    accc = accc_register(names)
    panel = panel_names()
    panel_codes = {c for c, n in names.items() for p in panel if norm(p) and norm(p) == norm(n)}
    kept = {d["code"] for d in (prev or {}).get("deals", [])}
    sources = {}
    for c in archive:
        sources.setdefault(c, set()).add("公告库")
    for c in feed_codes:
        sources.setdefault(c, set()).add("全市场公告流")
    for c in px:
        sources.setdefault(c, set()).add("价格信号")
    for c in accc:
        sources.setdefault(c, set()).add("ACCC")
    for c in panel_codes:
        sources.setdefault(c, set()).add("收购委员会")
    for c in kept:
        sources.setdefault(c, set()).add("昨天在榜")
    for c in liquid(live):   # weekly sweep of every liquid stock's ASX list (scrip deals don't pin the price)
        sources.setdefault(c, set()).add("全市场扫描")
    live_set = set(live)
    cands = sorted((c for c in sources if c in live_set), key=lambda c: (sources[c] == {"全市场扫描"}, c))
    print(f"[merger] candidates {len(cands)} (archive {len(archive)}, feed {len(feed_codes)}, price {len(px)}, accc {len(accc)}, panel {len(panel_codes)})")
    manual = load_manual()
    deals, skipped = [], {}
    for c in cands:
        if time.time() - t0 > 60 * float(os.environ.get("MERGER_MINUTES", "80")):
            skipped[c] = "time budget"
            continue
        items = (archive.get(c) or {}).get("items") or []
        if "公告库" not in sources[c] or not items:
            try:
                items = listings(c, 20 * 3600 if sources[c] - {"全市场扫描"} else 6 * 86400)
            except Exception as e:
                skipped[c] = f"list failed {type(e).__name__}"
                continue
        if not items:
            skipped[c] = "ASX list empty (retry next run)"
            continue
        d, why = deal_for(c, items, archive, names)
        if not d:
            skipped[c] = why
            continue
        if c in accc:
            a = accc[c]
            d.update({"accc_case": a["case"], "accc_stage": a["stage"], "accc_status": a["status"], "accc_url": a["url"]})
            d["src"]["accc"] = {"headline": a["title"], "date": TODAY.isoformat(), "url": a["url"]}
        for k, v in (manual.get(c) or {}).items():   # hand-read facts win; each carries its own source
            if k == "src":
                d["src"].update(v)
            else:
                d[k] = v
                d["manual"] = True
        d["found_by"] = sorted(sources[c])
        price_deal(d, idx)
        d["next_date"], d["next_label"] = next_key_date(d)
        deals.append(d)
    on = {d["code"]: d for d in deals}
    for d in list(deals):   # a pair pointing at each other: the one whose price makes no sense is the acquirer
        o = on.get(d.get("acquirer_code") or "")
        if d.get("pending") and o and o.get("acquirer_code") == d["code"] and not o.get("pending"):
            deals.remove(d)
            skipped[d["code"]] = f"acquirer of {o['code']}"
    deals.sort(key=lambda d: (d.get("pending") is not None or (d.get("after_tax") or {}).get("0.325", {}).get("ann_pct") is None,
                              -((d.get("after_tax") or {}).get("0.325", {}).get("ann_pct") or 0)))
    msgs = alerts(prev, deals)
    pushed = ntfy("并购套利", "\n".join(msgs[:12])) if msgs else False
    missed = sorted(c for c in accc if c not in {d["code"] for d in deals})
    out = {"generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "price_day": last_day, "deals": deals,
           "counts": {"candidates": len(cands), "deals": len(deals), "archive": len(archive), "feed": len(feed_codes),
                      "price_signal": len(px), "accc": len(accc), "panel": len(panel_codes)},
           "feed_backfill": feed_note, "accc_debug": ACCC_DEBUG, "panel_names": panel[:60], "accc_not_on_board": {c: skipped.get(c, "") for c in missed},
           "skipped": skipped, "alerts": msgs, "alerts_pushed": pushed,
           "rates": ["0%", "32.5%", "47%"], "note": "价格 = EODHD 最后收盘；完成日标「估计」的是按 HSF FY26 平均天数推的；拿不准的标「待核」。不构成下单。"}
    jsave(OUT, out)
    print(f"[merger] {len(deals)} deals on the board, {len(msgs)} alerts ({'pushed' if pushed else 'not pushed'}), {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
