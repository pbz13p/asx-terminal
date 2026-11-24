"""Round 100: shareholder structure of ASX small caps -- how concentrated (top 20, top 1, top 2), is the CEO / a director
among the biggest holders, and does the structure or its change tell us a stock is about to run (mainly up)?

Frank 2026-11-25 (起因 CND: top 20 hold ~42%, the largest holder ~5%).
Stage 1 (this file, S100_STAGE=probe): look at what the free announcement archive actually holds before fixing any rule:
  * annual reports ("Annual Report", "Appendix 4G" companions): how many contain the ASX Listing Rule 4.10 shareholder
    section ("twenty largest" / "top 20" holders, "substantial shareholders", "distribution of equity securities");
    20 sample text windows so the parser can be written against real layouts;
  * substantial holder notices (becoming / change / ceasing): count by year; 10 sample texts (holder name, %, date);
  * director interest notices (Appendix 3Y): count, and how many say on-market purchase / sale.
No result about returns is looked at in this stage; the rules for the return study are written after it.
"""
import gzip
import json
import os
import random
import re
import time
from collections import Counter
from pathlib import Path

import study

OUT = Path(os.environ.get("STUDY100_OUT", "out/study100_results.json"))
TOP = re.compile(r"(twenty|20) largest|top (twenty|20)|largest (twenty|20)", re.I)


def main():
    t0 = time.time()
    rng = random.Random(100)
    files = sorted((study.DATA / "ann").glob("*.json.gz"))
    ar_n = ar_top = 0
    ar_samples, sub_samples = [], []
    sub_by_year, y3_n, y3_onmkt_buy, y3_onmkt_sell = Counter(), 0, 0, 0
    codes_with_top = set()
    for f in files:
        try:
            d = json.loads(gzip.decompress(f.read_bytes()))
        except Exception:
            continue
        code = d.get("code", f.name[:-8])
        docs = d.get("docs", {})
        for it in d.get("listed", []):
            h = it.get("headline", "")
            t = (docs.get(it.get("ids")) or {}).get("text", "")
            if re.search(r"annual report", h, re.I):
                ar_n += 1
                m = TOP.search(t)
                if m:
                    ar_top += 1
                    codes_with_top.add(code)
                    if rng.random() < 0.01 and len(ar_samples) < 20:
                        ar_samples.append({"code": code, "date": it.get("date"), "head": h[:80], "full": bool((docs.get(it.get("ids")) or {}).get("full")),
                                           "window": t[max(0, m.start() - 300):m.start() + 2600]})
            elif re.search(r"substantial holder|substantial holding", h, re.I):
                sub_by_year[it.get("date", "")[:4]] += 1
                if t and rng.random() < 0.002 and len(sub_samples) < 10:
                    sub_samples.append({"code": code, "date": it.get("date"), "head": h[:80], "text": t[:1800]})
            elif re.search(r"director'?s? interest", h, re.I):
                y3_n += 1
                tl = t.lower()
                if "on-market" in tl or "on market" in tl:
                    if re.search(r"purchase|acqui|bought", tl):
                        y3_onmkt_buy += 1
                    if re.search(r"\bsale\b|\bsold\b|disposal", tl):
                        y3_onmkt_sell += 1
    out = {"round": 100, "stage": "probe", "files": len(files), "annual_reports": ar_n, "annual_reports_with_top20": ar_top,
           "companies_with_top20": len(codes_with_top), "substantial_notices_by_year": dict(sorted(sub_by_year.items())),
           "director_notices": y3_n, "director_onmarket_buy_mentions": y3_onmkt_buy, "director_onmarket_sell_mentions": y3_onmkt_sell,
           "annual_report_samples": ar_samples, "substantial_samples": sub_samples, "seconds": round(time.time() - t0)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("[100] probe done", out["seconds"], "s", flush=True)


if __name__ == "__main__":
    main()
