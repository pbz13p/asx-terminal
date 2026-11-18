"""Round 68: build the private ASX announcement archive for ~300 small/micro caps (up to 5 years each).

Universe: stocks trading now with market cap $10M-$200M (ASX company API) and 1-year median value traded >= $20k/day,
largest turnover first, up to ANN_N (default 300). For each: yearly announcement lists (asx.com.au), every PDF
downloaded and turned into text (first 8 pages + last 5, which hold the Appendix 5B/4C cash tables).

Resumable: each company is kept in data/ann/{code}.json.gz (Actions cache, private to the repo); finished
announcements are skipped next time. Stops after ANN_MINUTES (default 270) and reports what is left, so the
workflow can start itself again. Nothing is published in plain text.
"""
import gzip
import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import study
import study67
import volml

ANN = study.DATA / "ann"
N = int(os.environ.get("ANN_N", "5000"))  # 300 -> 600 -> 900 small caps first, then every other listed stock
BUDGET = float(os.environ.get("ANN_MINUTES", "230")) * 60
YEARS = list(range(date.today().year - 4, date.today().year + 1))
FROM_YEAR = int(os.environ.get("ANN_FROM_YEAR", "2015"))   # 2026-11-17 (Frank): back-fill older years and delisted companies
THIS_YEAR = date.today().year


def universe():
    f = ANN / "universe.json"
    chosen = json.loads(f.read_text()) if f.exists() else []
    if len(chosen) >= N:   # (N is 5000, above the ~1,500 actively traded companies, so this re-checks every run)
        return chosen
    have = {u["code"] for u in chosen}
    last = study.load("AXJO.INDX")[-1][0]
    cands, ipos = [], []
    for p in study.CACHE.glob("*.AU.json"):
        rows = study.load(p.name[:-5])
        if rows and rows[-1][0] >= last[:8] + "01" and len(rows) < 120 and p.name[:-8] not in have:
            ipos.append(p.name[:-8])   # 2026-11-17 (Frank): new listings / IPOs are followed from day one, not after 6 months
            continue
        if len(rows) < 120 or rows[-1][0] < last[:8] + "01":
            continue
        val = [r[6] * r[5] for r in rows[-250:]]
        med = statistics.median(val)
        if rows[-1][6] >= 0.001 and p.name[:-8] not in have:   # 2026-11-17 (Frank): thinly traded stocks too (were >= $1k/day)
            cl = [r[4] for r in rows[-1260:] if r[4]]
            run_min, peak = float("inf"), 1.0  # biggest rise from a low to a later high within 5 years
            for c in cl:
                run_min = min(run_min, c)
                peak = max(peak, c / run_min)
            cands.append((med, p.name[:-8], peak))
    cands.sort(reverse=True)
    # tiers 1-3 (small caps, by turnover): 300 ($10M-$200M, >=$20k/day) -> 600 ($5M-$300M, >=$10k) -> 900 ($3M-$500M, >=$5k)
    for med, code, peak in cands:
        n = len(chosen)
        if n >= 900:
            break
        mc = volml.company_info(code).get("marketCap")
        lo, hi, vmin = (1e7, 2e8, 2e4) if n < 300 else (5e6, 3e8, 1e4) if n < 600 else (3e6, 5e8, 5e3)
        if mc and lo <= mc <= hi and med >= vmin:
            chosen.append({"code": code, "mcap": mc, "value_med": round(med), "tier": 1 + n // 300})
    # after the small-cap tiers (even when fewer than 900 qualified -- that bug stopped the queue at 864):
    taken = {u["code"] for u in chosen}
    rest = [c for c in cands if c[1] not in taken]
    # tier 4: stocks that rose >= 5x from a low to a later high within 5 years (small-to-big stories), biggest rise first
    # tier 5: everything else still trading, by turnover (mega caps end up last-ish)
    t4 = sorted([c for c in rest if c[2] >= 5 and c[0] >= 1e3], key=lambda c: -c[2])
    t5 = [c for c in rest if c[2] < 5 and c[0] >= 1e3]
    t7 = [c for c in rest if c[0] < 1e3]   # tier 7: under $1k/day -- downloaded and watched, left out of research by default
    print(f"[universe] small caps {len(chosen)}, tier 4 (>=5x in 5y) {len(t4)}, tier 5 {len(t5)}", flush=True)
    for tier, group in ((4, t4), (5, t5), (7, t7)):
        for med, code, peak in group:
            if len(chosen) >= N:
                break
            chosen.append({"code": code, "mcap": volml.company_info(code).get("marketCap"), "value_med": round(med),
                           "tier": tier, "peak_multiple_5y": round(peak, 1)})
    gone = []   # tier 8: companies that stopped trading since FROM_YEAR (delisted / taken over / suspended) -- survivorship
    taken = {u["code"] for u in chosen} | set(ipos)
    for p in study.CACHE.glob("*.AU.json"):
        code = p.name[:-8]
        if code in taken or code in have:
            continue
        rows = study.load(p.name[:-5])
        if rows and rows[-1][0] < last[:8] + "01" and rows[-1][0][:4] >= str(FROM_YEAR):
            gone.append({"code": code, "tier": 8, "delisted": True, "from": int(rows[0][0][:4]), "to": int(rows[-1][0][:4])})
    chosen += sorted(gone, key=lambda u: -u["to"])
    print(f"[universe] delisted companies added: {len(gone)}", flush=True)
    for code in sorted(ipos):   # tier 6: new listings, always added (not capped by N)
        chosen.append({"code": code, "mcap": volml.company_info(code).get("marketCap"), "tier": 6, "new_listing": True})
    print(f"[universe] new listings added: {len(ipos)}", flush=True)
    ANN.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(chosen))
    return chosen


def load(code):
    f = ANN / f"{code}.json.gz"
    return json.loads(gzip.decompress(f.read_bytes())) if f.exists() else {"code": code, "listed": [], "docs": {}}


def save(code, d):
    (ANN / f"{code}.json.gz").write_bytes(gzip.compress(json.dumps(d, ensure_ascii=False).encode()))


def main():
    t0 = time.time()
    U = universe()
    k, n = (int(x) for x in os.environ.get("ANN_SHARD", "0/1").split("/"))   # N jobs side by side, each every N-th company
    U = [u for i, u in enumerate(U) if i % n == k]
    if n > 1:   # each shard's cache holds only its own companies, so restoring all shard caches never puts back a stale copy
        mine = {u["code"] for u in U}
        for f in ANN.glob("*.json.gz"):
            if f.name[:-8] not in mine:
                f.unlink()
    # 2026-11-19: companies never started go first. In list order the daily re-listing of the first ~1,800 used up every
    # run's time budget, so the 1,839 at the tail (mostly delisted companies) were never reached ("not started: 613" x3)
    U.sort(key=lambda u: (ANN / f"{u['code']}.json.gz").exists())
    print("universe", len(U), "companies (shard", f"{k}/{n})")
    todo_total = done_now = failed = 0
    for i, u in enumerate(U):
        code = u["code"]
        if time.time() - t0 > BUDGET or study67.BRAKE["stop"]:
            continue
        d = load(code)
        # back-fill: every past year from FROM_YEAR (or the company's first trading year) is listed once and remembered
        u0 = u
        first, last_y = max(FROM_YEAR, u0.get("from", FROM_YEAR)), u0.get("to", THIS_YEAR)
        past = [y for y in range(first, min(last_y, THIS_YEAR - 1) + 1) if y not in d.get("years_done", []) and y < YEARS[0]]
        if past and (d["listed"] or u0.get("delisted")) and time.time() - t0 < BUDGET and not study67.BRAKE["stop"]:
            seen = {it["ids"] for it in d["listed"]}
            for y in past:
                f0 = study67.calls["failed"]
                got = study67.listing(code, [y])
                if study67.calls["failed"] > f0:
                    break
                d["listed"] += [it for it in got if it["ids"] not in seen]
                seen |= {it["ids"] for it in got}
                d.setdefault("years_done", []).append(y)
            save(code, d)
        if (not d["listed"] or d.get("listed_on") != date.today().isoformat() or not d.get("flag_v2")) and not (u0.get("delisted") and d["listed"]):
            # first time: the last 5 years; afterwards only this year and last year (older years never change) -- half the requests
            yrs = [y for y in YEARS if y <= last_y and (not d.get("listed_on") or y >= THIS_YEAR - 1)]
            items = study67.listing(code, yrs) if yrs else []
            if items or (yrs == [] and d["listed"]):
                old_ids = {it["ids"] for it in items}
                items += [it for it in d["listed"] if it["ids"] not in old_ids]   # keep back-filled years
                d["listed"], d["listed_on"] = items, date.today().isoformat()
                # 2026-11-15: the old parser marked every announcement price-sensitive; correct stored docs from the fresh list
                flag = {it["ids"]: it["sensitive"] for it in items}
                for ids, doc in d["docs"].items():
                    if ids in flag:
                        doc["sensitive"] = flag[ids]
                d["flag_v2"] = True
                save(code, d)
            elif not d["listed"]:   # 2026-11-17: no list (delisted / ASX error) -- give up after 3 tries, or the job restarts forever
                d["list_tries"] = d.get("list_tries", 0) + 1
                save(code, d)
        # new announcements, plus earlier failures (up to 3 tries each)
        # plus price-sensitive docs saved before full-text storage (2026-11-15) that were cut (over 8 pages or 8000 chars)
        todo = [it for it in d["listed"] if it["ids"] not in d["docs"]
                or ("error" in d["docs"][it["ids"]] and d["docs"][it["ids"]].get("tries", 1) < 3)
                or (it["sensitive"] and not d["docs"][it["ids"]].get("full") and "error" not in d["docs"][it["ids"]]
                    and (d["docs"][it["ids"]].get("pages", 0) > 8 or len(d["docs"][it["ids"]].get("text", "")) >= 7900))]
        todo_total += len(todo)

        def fetch(it):
            try:
                text, url, pages = study67.pdf_text(it)
                return it["ids"], {"date": it["date"], "headline": it["headline"], "sensitive": it["sensitive"],
                                   "pages": pages, "text": text, "full": True}
            except Exception as e:
                prev = d["docs"].get(it["ids"], {})
                return it["ids"], {"date": it["date"], "headline": it["headline"], "sensitive": it["sensitive"],
                                   "error": f"{type(e).__name__}"[:60], "tries": prev.get("tries", 1) + ("error" in prev)}
        with ThreadPoolExecutor(max_workers=4) as ex:
            for k in range(0, len(todo), 40):
                if time.time() - t0 > BUDGET or study67.BRAKE["stop"]:
                    break
                for ids, doc in ex.map(fetch, todo[k:k + 40]):
                    d["docs"][ids] = doc
                    done_now += 1
                    failed += "error" in doc
                save(code, d)
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(U)} companies, {done_now} docs this run, {failed} failed, {(time.time() - t0) / 60:.0f} min")
    left = total_docs = unlisted = 0
    for u in U:
        d = load(u["code"])
        if not d["listed"] and d.get("list_tries", 0) < 3:
            unlisted += 1
        left += len([it for it in d["listed"] if it["ids"] not in d["docs"]
                     or ("error" in d["docs"][it["ids"]] and d["docs"][it["ids"]].get("tries", 1) < 3)
                     or (it["sensitive"] and not d["docs"][it["ids"]].get("full") and "error" not in d["docs"][it["ids"]]
                         and (d["docs"][it["ids"]].get("pages", 0) > 8 or len(d["docs"][it["ids"]].get("text", "")) >= 7900))])
        total_docs += len(d["docs"])
    left += unlisted  # companies whose list was never fetched also count as unfinished
    failed_now = sum(1 for u in U for doc in load(u["code"])["docs"].values() if "error" in doc)
    out = {"failed_total": failed_now, "generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "companies": len(U), "docs_total": total_docs,
           "docs_this_run": done_now, "companies_not_started": unlisted, "failed_this_run": failed, "left": left, "minutes": round((time.time() - t0) / 60, 1),
           "universe": U}
    p = Path(os.environ.get("STUDY68_OUT", "out/study68_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    (study.DATA / ".ann_left").write_text(str(left))
    (study.DATA / ".ann_brake").write_text("1" if study67.BRAKE["stop"] else "0")
    if study67.BRAKE["stop"]:
        print("[brake] ASX refused us 20 times this run; stopped early, progress saved", flush=True)
    print("[done]", json.dumps({k: out[k] for k in out if k != "universe"}))


if __name__ == "__main__":
    main()
