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
N = int(os.environ.get("ANN_N", "900"))
BUDGET = float(os.environ.get("ANN_MINUTES", "230")) * 60
YEARS = list(range(date.today().year - 4, date.today().year + 1))


def universe():
    f = ANN / "universe.json"
    chosen = json.loads(f.read_text()) if f.exists() else []
    if len(chosen) >= N:
        return chosen
    have = {u["code"] for u in chosen}
    last = study.load("AXJO.INDX")[-1][0]
    cands = []
    for p in study.CACHE.glob("*.AU.json"):
        rows = study.load(p.name[:-5])
        if len(rows) < 120 or rows[-1][0] < last[:8] + "01":
            continue
        val = [r[6] * r[5] for r in rows[-250:]]
        med = statistics.median(val)
        if med >= 1e4 and rows[-1][6] >= 0.005 and p.name[:-8] not in have:
            cands.append((med, p.name[:-8]))
    cands.sort(reverse=True)
    for med, code in cands:
        mc = volml.company_info(code).get("marketCap")
        # first 300: $10M-$200M and >= $20k/day; the next ones widen to $5M-$300M and >= $10k/day
        lo, hi, vmin = (1e7, 2e8, 2e4) if len(chosen) < 300 else (5e6, 3e8, 1e4) if len(chosen) < 600 else (3e6, 5e8, 5e3)
        if mc and lo <= mc <= hi and med >= vmin:
            chosen.append({"code": code, "mcap": mc, "value_med": round(med)})
        if len(chosen) >= N:
            break
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
    print("universe", len(U), "companies")
    todo_total = done_now = failed = 0
    for i, u in enumerate(U):
        code = u["code"]
        if time.time() - t0 > BUDGET:
            continue
        d = load(code)
        if not d["listed"] or d.get("listed_on") != date.today().isoformat():
            items = study67.listing(code, YEARS)
            if items:
                d["listed"], d["listed_on"] = items, date.today().isoformat()
        # new announcements, plus earlier failures (up to 3 tries each)
        todo = [it for it in d["listed"] if it["ids"] not in d["docs"]
                or ("error" in d["docs"][it["ids"]] and d["docs"][it["ids"]].get("tries", 1) < 3)]
        todo_total += len(todo)

        def fetch(it):
            try:
                text, url, pages = study67.pdf_text(it)
                return it["ids"], {"date": it["date"], "headline": it["headline"], "sensitive": it["sensitive"],
                                   "pages": pages, "text": text}
            except Exception as e:
                prev = d["docs"].get(it["ids"], {})
                return it["ids"], {"date": it["date"], "headline": it["headline"], "sensitive": it["sensitive"],
                                   "error": f"{type(e).__name__}"[:60], "tries": prev.get("tries", 1) + ("error" in prev)}
        with ThreadPoolExecutor(max_workers=4) as ex:
            for k in range(0, len(todo), 40):
                if time.time() - t0 > BUDGET:
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
        if not d["listed"]:
            unlisted += 1
        left += len([it for it in d["listed"] if it["ids"] not in d["docs"]
                     or ("error" in d["docs"][it["ids"]] and d["docs"][it["ids"]].get("tries", 1) < 3)])
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
    print("[done]", json.dumps({k: out[k] for k in out if k != "universe"}))


if __name__ == "__main__":
    main()
