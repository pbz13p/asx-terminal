"""Round 97: is Haiku good enough to read the remaining ~227k announcements, or is DeepSeek (much cheaper) as good?

Frank 2026-11-21 ("Haiku 会不会读不懂？DeepSeek 质量怎么样？用订阅额度测"). Plan agreed:
  * 200 announcements Haiku already scored in round 76 (cached, no new Claude cost): 100 short (one part, <= 6,000
    anonymised characters) + 100 of any length. Same frozen anonymiser and the same prompt as round 76.
  * DeepSeek V4 Flash scores the same 200 (whole text in one request; it has a 1M-token window).
  * Reference: Claude (Opus, in the chat, on Frank's subscription) scores 50 of the short ones blind from the texts this
    script exports -- the answers of both models sit in a separate key and are read only after Claude's own scores exist.
  * Hard truth where code can read it: Appendix 5B item 8.7 "estimated quarters of funding available".
  * DeepSeek must also pass round 76's two leakage tests: "which company is this?" on 200 anonymised excerpts (<= 5%
    correct) and "ticker + date: up or down next month?" on 300 pre-cutoff cases (>= 80% UNKNOWN, answered ones not
    clearly better than a coin).
Spend: DeepSeek balance before/after (/user/balance) plus token prices; logged in data/ai/spend.json with every other AI cost.
"""
import json
import os
import random
import re
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import study76 as S

DS_MODEL = os.environ.get("S97_MODEL", "deepseek-flash")
DS_PRICE = {"in": 0.14, "in_hit": 0.028, "out": 0.28}     # USD per million tokens (V4 Flash list price, Aug 2026)
KEY = os.environ.get("DEEPSEEK_API_KEY", "")
H = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"}
OUT = Path(os.environ.get("STUDY97_OUT", "out/study97_results.json"))
CACHE = S.AI / "s97_ds"


def post(path, body=None):
    req = urllib.request.Request("https://api.deepseek.com" + path, headers=H, data=json.dumps(body).encode() if body else None)
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)


def balance():
    try:
        b = post("/user/balance")
        return sum(float(x["total_balance"]) for x in b.get("balance_infos", []) if x.get("currency") == "USD")
    except Exception:
        return None


def ask(tag, system, user, max_tokens):
    f = CACHE / f"{tag}.json"
    if f.exists():
        return json.loads(f.read_text())
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
    for attempt in range(4):
        try:
            r = post("/chat/completions", {"model": DS_MODEL, "max_tokens": max_tokens, "temperature": 0, "messages": msgs})
            d = {"text": (r["choices"][0]["message"].get("content") or "").strip(), "usage": r.get("usage") or {}}
            f.write_text(json.dumps(d, ensure_ascii=False))
            return d
        except Exception as e:
            print("retry", tag, attempt, type(e).__name__, flush=True)
            time.sleep(15 * (attempt + 1))
    return {"text": "", "usage": {}, "failed": True}


def cash_truth(text):
    """Appendix 5B item 8.7: estimated quarters of funding available (a number, or 'N/A' / '> 8')."""
    # skip the "(item 8.4 divided by item 8.3)" wording, whose digits were read as the answer in the first run
    m = re.search(r"8\.7\s*Estimated quarters of funding available\s*(?:\([^)]*\))?[^0-9N>(]{0,80}?(>?\s*\d+(?:\.\d+)?|N/?A)", text, re.I | re.S)
    if not m:
        return None
    v = m.group(1).replace(" ", "")
    if v.upper().startswith("N"):
        return None
    return 9.0 if v.startswith(">") else float(v)


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def agree(a, b):
    """Field-by-field agreement of two score dicts."""
    out = {}
    out["type"] = a.get("type") == b.get("type")
    ia, ib = num(a.get("importance")), num(b.get("importance"))
    out["importance_exact"] = ia is not None and ia == ib
    out["importance_pm1"] = ia is not None and ib is not None and abs(ia - ib) <= 1
    out["vs_prior"] = a.get("vs_prior") == b.get("vs_prior")
    for k in ("dilution_risk", "hype"):
        x, y = num(a.get(k)), num(b.get(k))
        out[k + "_pm1"] = x is not None and y is not None and abs(x - y) <= 1
    ga = (ia or 0) >= 4 and a.get("vs_prior") == "above" and (num(a.get("hype")) or 0) <= 1 and (num(a.get("dilution_risk")) or 0) <= 1
    gb = (ib or 0) >= 4 and b.get("vs_prior") == "above" and (num(b.get("hype")) or 0) <= 1 and (num(b.get("dilution_risk")) or 0) <= 1
    out["clean_good_same"] = ga == gb
    av = lambda s: (num(s.get("dilution_risk")) or 0) >= 3 or s.get("type") == "capital_raising" or (num(s.get("hype")) or 0) >= 3
    out["avoid_same"] = av(a) == av(b)
    return out


def main():
    if not KEY:
        raise SystemExit("DEEPSEEK_API_KEY missing")
    CACHE.mkdir(parents=True, exist_ok=True)
    common = set(json.loads((S.AI / "s76_common.json").read_text()))
    docs = S.docs()
    by_code = {}
    for x in docs:
        by_code.setdefault(x["code"], []).append(x)
    haiku = {}
    for x in docs:
        f = S.CACHE / f"{x['id']}.json"
        if f.exists():
            haiku[x["id"]] = json.loads(f.read_text())
    scored = [x for x in docs if x["id"] in haiku]
    rnd = random.Random(97)
    anon = {}

    def A(x):
        if x["id"] not in anon:
            anon[x["id"]] = S.anonymise(x["headline"] + ". " + x["text"], x["code"], common)
        return anon[x["id"]]
    pool = rnd.sample(scored, min(3000, len(scored)))
    short = [x for x in pool if len(A(x)) <= 6000][:100]
    rest = [x for x in pool if x not in short][:100]
    sample = short + rest
    # 5B truth: add up to 40 quarterly reports with item 8.7 so the cash field is actually tested
    q = [x for x in pool if x not in sample and cash_truth(x["text"]) is not None][:40]
    sample += q
    print("[97] sample", len(sample), "short", len(short), "with 5B truth", sum(cash_truth(x["text"]) is not None for x in sample), flush=True)
    b0 = balance()
    jobs = []
    for i, x in enumerate(sample):
        user = f"PRIOR (earlier announcement of the same kind):\n{S.prior_text(x, by_code, common)}\n\nANNOUNCEMENT:\n{A(x)[:400000]}"
        jobs.append((f"s_{x['id']}", S.SYSTEM, user, 8000))
    probe = rnd.sample(docs, 200)
    for i, x in enumerate(probe):
        jobs.append((f"d_{x['id']}", None, "Below is an anonymised excerpt of an ASX company announcement. Which ASX-listed company "
                     "issued it? Reply with only its ASX ticker, or UNKNOWN if you cannot tell.\n\n" + A(x)[:3000], 2000))
    recall = rnd.sample([x for x in docs if x["date"] < "2026-04-01"], 300)
    for i, x in enumerate(recall):
        jobs.append((f"r_{x['id']}", None, f"ASX ticker {x['code']}. Starting from {x['date']}, did its share price go up or down over the "
                     "following month? If you do not actually know, say UNKNOWN. Reply with one word: UP, DOWN or UNKNOWN.", 2000))
    res = {}
    with ThreadPoolExecutor(8) as ex:
        for tag, d in zip([j[0] for j in jobs], ex.map(lambda j: ask(*j), jobs)):
            res[tag] = d
    b1 = balance()
    usage = Counter()
    for d in res.values():
        u = d.get("usage") or {}
        usage["in"] += u.get("prompt_tokens", 0)
        usage["in_hit"] += u.get("prompt_cache_hit_tokens", 0)
        usage["out"] += u.get("completion_tokens", 0)
    usd_tokens = ((usage["in"] - usage["in_hit"]) * DS_PRICE["in"] + usage["in_hit"] * DS_PRICE["in_hit"] + usage["out"] * DS_PRICE["out"]) / 1e6
    usd = round(b0 - b1, 4) if b0 is not None and b1 is not None and b0 >= b1 else round(usd_tokens, 4)
    L = S.ledger()
    L["entries"].append({"date": date.today().isoformat(), "what": "round 97 DeepSeek vs Haiku test (240 scored + 200 de-anon + 300 recall)",
                         "usd": usd, "provider": "deepseek", "input_tokens": usage["in"], "output_tokens": usage["out"]})
    L["total"] = round(sum(e["usd"] for e in L["entries"]), 4)
    S.LEDGER.write_text(json.dumps(L, ensure_ascii=False, indent=1))
    # compare
    rows, grade = [], []
    agg = Counter()
    n = 0
    cash = {"haiku": [], "deepseek": []}
    for x in sample:
        ds = S.parse_json(res[f"s_{x['id']}"]["text"]) or {}
        hk = haiku[x["id"]]
        if ds:
            n += 1
            for k, v in agree(hk, ds).items():
                agg[k] += v
        t = cash_truth(x["text"])
        if t is not None:
            for name, sc in (("haiku", hk), ("deepseek", ds)):
                v = num(sc.get("cash_quarters"))
                cash[name].append({"truth": t, "got": v})
        rows.append({"id": x["id"], "short": x in short, "haiku": {k: hk.get(k) for k in ("type", "importance", "vs_prior", "dilution_risk",
                     "cash_quarters", "hype", "one_line")}, "deepseek": ds, "cash_5b": t})
        if x in short[:50]:
            grade.append({"id": x["id"], "prior": S.prior_text(x, by_code, common)[:1200], "text": A(x)})

    def cash_score(lst):
        have = [c for c in lst if c["got"] is not None]
        ok = [c for c in have if abs(c["got"] - c["truth"]) <= 0.5 or (c["truth"] >= 8 and c["got"] >= 8)]
        return {"n_truth": len(lst), "answered": len(have), "within_half_quarter": len(ok)}
    hits = sum(1 for x in probe if res[f"d_{x['id']}"]["text"].strip().upper().strip(".").startswith(x["code"]))
    ans, right, n_ans = Counter(), 0, 0
    for x in recall:
        a = res[f"r_{x['id']}"]["text"].strip().upper()
        a = "UP" if a.startswith("UP") else "DOWN" if a.startswith("DOWN") else "UNKNOWN"
        ans[a] += 1
        if a != "UNKNOWN":
            r = S.fwd21(x["code"], x["date"])
            if r:
                n_ans += 1
                right += (r > 0) == (a == "UP")
    out = {"round": 97, "built": time.strftime("%Y-%m-%d"), "model": DS_MODEL, "sample": len(sample), "deepseek_parsed": n,
           "failed_calls": sum(1 for d in res.values() if d.get("failed")),
           "agreement_pct": {k: round(100 * v / max(1, n), 1) for k, v in agg.items()},
           "cash_5b": {k: cash_score(v) for k, v in cash.items()},
           "deanon_hit_pct": round(100 * hits / len(probe), 1),
           "recall": dict(ans), "recall_unknown_pct": round(100 * ans["UNKNOWN"] / 300, 1),
           "recall_answered_accuracy_pct": round(100 * right / n_ans, 1) if n_ans else None, "recall_answered_n": n_ans,
           "usd": usd, "usd_from_tokens": round(usd_tokens, 4), "tokens": dict(usage),
           "usd_per_doc_deepseek": round(usd_tokens * 0 + (sum(((res[f"s_{x['id']}"].get('usage') or {}).get('prompt_tokens', 0) * DS_PRICE['in']
                                                                  + (res[f"s_{x['id']}"].get('usage') or {}).get('completion_tokens', 0) * DS_PRICE['out'])
                                                                 for x in sample) / 1e6) / max(1, len(sample)), 6),
           "grade": grade, "rows": rows}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("[97] done; parsed", n, "of", len(sample), flush=True)   # numbers stay in the encrypted file


if __name__ == "__main__":
    main()
