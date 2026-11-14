"""Round 69: rule-based extraction from the announcement archive (round 68) + event study. No AI, no cost.

From each announcement text: type (headline rules), Appendix 5B/4C cash and quarters of funding, director on-market
buys/sells (Appendix 3Y), placement/raising price. Then, for each event type, what the share price did afterwards
(20 / 60 / 250 trading days, from the next close) compared with every small cap over the same period, how often
it doubled within a year, how often it halved.
"""
import bisect
import gzip
import json
import os
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

import study

ANN = study.DATA / "ann"
TYPES = [
    ("季度现金报告", r"quarterly|appendix 5b|appendix 4c|cash ?flow report"),
    ("董事持股变动", r"director'?s'? interest|appendix 3y"),
    ("配股/融资", r"placement|capital raising|entitlement|rights issue|share purchase plan|raise[sd]? \$|\$[\d.]+ ?m(illion)? (raising|placement)"),
    ("停牌", r"trading halt|suspension from"),
    ("钻探/化验结果", r"drill|assay|intercept|intersect|discovery|high[- ]grade|mineralis"),
    ("资源量/储量", r"resource|reserve|jorc"),
    ("许可/批准", r"licen[cs]e|permit|approval|granted|approved"),
    ("收购资产/期权", r"acqui|option to acquire|binding term sheet|farm[- ]?in|farm[- ]?out"),
    ("被收购/协议安排", r"takeover|scheme|bidder|indicative proposal|non-binding"),
    ("合同/订单", r"contract|agreement|offtake|order|tender|award"),
    ("临床/监管", r"phase [123i]|clinical|fda|tga|trial"),
    ("大股东变动", r"substantial (holder|holding)"),
]


def classify(head):
    h = head.lower()
    return [name for name, pat in TYPES if re.search(pat, h)]


def num(s):
    s = s.replace(",", "").strip()
    neg = s.startswith("(")
    try:
        v = float(s.strip("()"))
    except ValueError:
        return None
    return -v if neg else v


def cash_info(text):
    t = re.sub(r"\s+", " ", text)
    out = {}
    m = re.search(r"Estimated quarters of funding available\s*(?:\([^)]*\))?\s*(\d+(?:\.\d+)?|N/?A)", t, re.I)
    if m:
        out["quarters"] = None if m.group(1).upper().startswith("N") else float(m.group(1))
    m = re.search(r"Total available funding\s*(?:\([^)]*\))?\s*([\d,]+)", t, re.I)
    if m:
        out["funding_k"] = num(m.group(1))
    return out


def director_trade(text):
    t = re.sub(r"\s+", " ", text)
    on_market = bool(re.search(r"on[- ]market (trade|purchase|acquisition|buy)", t, re.I))
    vals = [num(v) for v in re.findall(r"Value/Consideration.{0,160}?\$\s?([\d,]+(?:\.\d+)?)", t, re.I)]
    vals = [v for v in vals if v]
    disposed = re.search(r"Number disposed\s*(?!nil|-|N/A)([\d,]{3,})", t, re.I)
    return {"on_market": on_market, "value": sum(vals) if vals else None, "sold": bool(disposed)}


def raise_price(text):
    t = re.sub(r"\s+", " ", text[:5000])
    m = re.search(r"(?:at|price of|issue price of)\s*\$\s?(0?\.\d+|\d+\.\d+)\s*(?:per|a)\s*(?:new\s*)?share", t, re.I) \
        or re.search(r"(?:at|price of)\s*(\d+(?:\.\d+)?)\s*(?:cents|c)\s*(?:per|a)\s*(?:new\s*)?share", t, re.I)
    if not m:
        return None
    v = float(m.group(1))
    return v / 100 if "cent" in m.group(0).lower() or re.search(r"\dc\s*(per|a)", m.group(0)) else v


def main():
    events = []
    for f in sorted(ANN.glob("*.json.gz")):
        d = json.loads(gzip.decompress(f.read_bytes()))
        code = d["code"]
        for ids, doc in d["docs"].items():
            if "text" not in doc:
                continue
            types = classify(doc["headline"])
            e = {"code": code, "date": doc["date"], "headline": doc["headline"][:120], "sensitive": doc["sensitive"], "types": types}
            if "季度现金报告" in types:
                e.update(cash_info(doc["text"]))
            if "董事持股变动" in types:
                e.update(director_trade(doc["text"]))
            if "配股/融资" in types:
                p = raise_price(doc["text"])
                if p:
                    e["raise_price"] = p
            events.append(e)
    print("events", len(events), "companies", len({e["code"] for e in events}))
    # prices on the ASX 200 calendar
    cal = [r[0] for r in study.load("AXJO.INDX")]
    pos = {d: i for i, d in enumerate(cal)}
    px = {}
    for code in {e["code"] for e in events}:
        by = {r[0]: r for r in study.load(f"{code}.AU")}
        if not by:
            continue
        arr, last = [], None
        for d in cal:
            if d in by:
                last = by[d]
            arr.append((last[4], last[6]) if last else (np.nan, np.nan))
        px[code] = np.array(arr)
    # baseline: all archive companies, every day
    def fwd(code, d, h):
        a = px.get(code)
        if a is None:
            return None
        i = bisect.bisect_left(cal, d)
        if i >= len(cal) or i + h >= len(cal) or not np.isfinite(a[i, 0]) or a[i, 0] <= 0:
            return None
        seg = a[i:i + h + 1, 0]
        return seg[-1] / seg[0] - 1, np.nanmax(seg) / seg[0], a[i, 1]
    base = defaultdict(list)
    for code, a in px.items():
        for i in range(0, len(cal) - 250, 21):
            if np.isfinite(a[i, 0]) and a[i, 0] > 0 and cal[i] >= "2021-06":
                for h in (20, 60, 250):
                    base[h].append(a[i + h, 0] / a[i, 0] - 1)
                base["peak"].append(np.nanmax(a[i:i + 251, 0]) / a[i, 0])

    def summarise(sel, label):
        r = {"label": label, "n": len(sel)}
        for h in (20, 60, 250):
            v = [x for x in (fwd(e["code"], e["date"], h) for e in sel) if x]
            if len(v) >= 15:
                rets = np.array([x[0] for x in v])
                r[f"median_{h}"] = round(100 * float(np.median(rets)), 1)
                r[f"mean_{h}"] = round(100 * float(np.mean(np.clip(rets, -1, 9))), 1)
                if h == 250:
                    r["p_2x_peak"] = round(100 * float(np.mean([x[1] >= 2 for x in v])), 1)
                    r["p_halve"] = round(100 * float(np.mean(rets <= -.5)), 1)
                    r["n_250"] = len(v)
        return r
    out = {"events": len(events), "companies": len({e["code"] for e in events}),
           "baseline": {"median_20": round(100 * float(np.median(base[20])), 1), "median_60": round(100 * float(np.median(base[60])), 1),
                        "median_250": round(100 * float(np.median(base[250])), 1), "mean_250": round(100 * float(np.mean(np.clip(base[250], -1, 9))), 1),
                        "p_2x_peak": round(100 * float(np.mean(np.array(base["peak"]) >= 2)), 1),
                        "p_halve": round(100 * float(np.mean(np.array(base[250]) <= -.5)), 1)}, "by_type": [], "special": []}
    for name, _ in TYPES:
        sel = [e for e in events if name in e["types"] and e["sensitive"]]
        out["by_type"].append(summarise(sel, name + "（价格敏感）"))
    q = [e for e in events if "quarters" in e and e["quarters"] is not None]
    out["special"] += [summarise([e for e in q if e["quarters"] < 1], "现金 <1 个季度"),
                       summarise([e for e in q if 1 <= e["quarters"] < 2], "现金 1–2 个季度"),
                       summarise([e for e in q if 2 <= e["quarters"] < 4], "现金 2–4 个季度"),
                       summarise([e for e in q if e["quarters"] >= 4], "现金 ≥4 个季度")]
    dt = [e for e in events if e.get("on_market") and not e.get("sold") and e.get("value")]
    out["special"] += [summarise([e for e in dt if e["value"] < 2e4], "董事市场买入 <$2 万"),
                       summarise([e for e in dt if e["value"] >= 2e4], "董事市场买入 ≥$2 万"),
                       summarise([e for e in events if e.get("on_market") and e.get("sold")], "董事市场卖出")]
    disc = []
    for e in events:
        if "raise_price" in e:
            a = px.get(e["code"])
            if a is not None and e["date"] in pos:
                i = pos[e["date"]]
                prev = a[max(0, i - 3):i, 1]
                prev = prev[np.isfinite(prev)]
                if len(prev):
                    e["discount"] = e["raise_price"] / prev[-1] - 1
                    disc.append(e)
    out["special"] += [summarise([e for e in disc if e["discount"] <= -.2], "融资价比前收盘低 ≥20%"),
                       summarise([e for e in disc if -.2 < e["discount"] <= -.05], "融资价低 5–20%"),
                       summarise([e for e in disc if e["discount"] > -.05], "融资价接近或高于前收盘")]
    # latest state per company (for the app): last cash report, last director buy
    latest = {}
    for e in sorted(events, key=lambda e: e["date"]):
        s = latest.setdefault(e["code"], {})
        if "quarters" in e:
            s.update({"quarters": e["quarters"], "funding_k": e.get("funding_k"), "cash_date": e["date"]})
        if e.get("on_market") and not e.get("sold") and e.get("value"):
            s.update({"last_director_buy": e["date"], "director_buy_value": e["value"]})
        if "raise_price" in e:
            s.update({"last_raise": e["date"], "raise_price": e["raise_price"]})
    out["latest"] = latest
    p = Path(os.environ.get("STUDY69_OUT", "out/study69_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("events", "companies", "baseline")}, ensure_ascii=False))
    for r in out["by_type"] + out["special"]:
        print(r)


if __name__ == "__main__":
    main()
