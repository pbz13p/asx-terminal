"""Round 76: AI reads small-cap announcements (round 68 archive) and scores each one.

Stage "pilot" (default, S76_STAGE=pilot): one Message Batch with
  - 200 anonymised price-sensitive announcements scored as JSON,
  - 200 de-anonymisation probes ("which company is this?"),
  - 300 recall probes ("ticker + date: did it go up or down the next month?").
It prices the full run from the pilot's real token usage and writes the gate results.
Stage "full" scores every price-sensitive announcement not yet cached (or a proportional sample if the
estimate is over S76_MAX_USD) and runs the event study against round 69's rule types.

Rules (CLAUDE.md, "用 AI 读文本做研究"): one spend ledger (data/ai/spend.json, all AI spend), stop at $35 total;
results cached per announcement id in data/ai/s76/ (never pay twice); runs only on GitHub Actions.
Full text (2026-11-15): the AI reads the WHOLE announcement. Short ones in one request; long ones are cut at page
boundaries into parts, every part is read for notes (key facts, numbers, risks, page refs), then one more request
scores the announcement from all the notes plus the first part.
Model: claude-haiku-5-5 via the Batch API (half price). Reliable knowledge cutoff June 2026.
"""
import gzip
import json
import os
import random
import re
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import numpy as np

import study

ANN = study.DATA / "ann"
AI = study.DATA / "ai"
CACHE = AI / "s76"
LEDGER = AI / "spend.json"
STATE = AI / "s76_state.json"
MODEL = os.environ.get("S76_MODEL", "claude-haiku-5-5")
CUTOFF = "2026-07-01"          # first day after the model's reliable knowledge cutoff (June 2026)
PRICE = {"in": 0.05, "out": 0.25, "cache_read": 0.005, "cache_write": 0.0625}   # USD per MTok, Batch API, prompts < 100k
BUDGET_STOP = 95.0             # CLAUDE.md: total AI budget $100, stop at $95
API = "https://api.anthropic.com/v1/messages/batches"
CHARS = 6000                  # used only for the anonymiser's word statistics and the de-anonymisation probe
SEG = 24000                   # whole announcement is read; long ones in parts of ~24k chars (~6k tokens), every part read
OUT = Path(os.environ.get("STUDY76_OUT", "out/study76_results.json"))

SYSTEM = """You are an analyst reading ANONYMISED announcements from small companies listed on the Australian stock exchange.
Names of companies, people, projects and places are replaced by [N]; years by [Y]. Judge only from the text given.
Return ONLY one JSON object, no prose, with these keys:
{"type": one of "drilling_assay","resource_reserve","capital_raising","quarterly_report","financial_results","contract_order",
 "acquisition_divestment","permit_approval","clinical_regulatory","takeover_scheme","production_update","other",
 "importance": 1-5 (5 = could change the company's value a lot),
 "vs_prior": "above","inline","below" or "unknown" (compared with PRIOR, the company's earlier announcement of the same kind, if given),
 "dilution_risk": 0-3 (0 none, 3 large new share issue now or clearly needed soon),
 "cash_quarters": number of quarters of cash left if the text states or clearly implies it, else null,
 "hype": 0-3 (0 sober and factual, 3 promotional adjectives far ahead of the hard numbers),
 "numbers": {"grade": "...", "revenue": "...", "contract_value": "...", "raise_amount": "..."} short strings or null,
 "one_line": "at most 20 words, plain English"}"""


# ---------- data ----------
def docs():
    """Every price-sensitive announcement with text: list of dicts sorted by code, date."""
    out = []
    for f in sorted(ANN.glob("*.json.gz")):
        d = json.loads(gzip.decompress(f.read_bytes()))
        if not d.get("flag_v2"):          # price-sensitive flag not yet corrected for this company
            continue
        for ids, doc in d["docs"].items():
            if doc.get("sensitive") and doc.get("text"):
                out.append({"id": ids, "code": d["code"], "date": doc["date"][:10], "headline": doc["headline"], "text": doc["text"],
                            "full": bool(doc.get("full"))})
    out.sort(key=lambda x: (x["code"], x["date"]))
    return out


PLACES = re.compile(r"\b(?:Melbourne|Sydney|Perth|Brisbane|Adelaide|Hobart|Darwin|Canberra|Gold Coast|Subiaco|West Perth|"
                    r"North Sydney|Victoria|New South Wales|Queensland|Western Australia|South Australia|Tasmania|"
                    r"Northern Territory|VIC|NSW|QLD|WA|SA|TAS|NT|ACT)\b")
CAP = re.compile(r"\b[A-Z][A-Za-z'&\-]{1,}\b")


def build_anonymiser(all_docs, min_companies=None):
    """Capitalised words used by fewer than N companies are names (company, person, project, place).
    N = 20 at first; the 2026-11-16 pilot's de-anonymisation probe still guessed 21% of companies, so it is tunable (S76_ANON_MIN)."""
    min_companies = min_companies or int(os.environ.get("S76_ANON_MIN", "20"))
    seen = defaultdict(set)
    per = Counter()
    for x in all_docs:
        if per[x["code"]] >= 25:
            continue
        per[x["code"]] += 1
        for w in set(CAP.findall(x["headline"] + " " + x["text"][:CHARS])):
            seen[w.lower()].add(x["code"])
    common = {w for w, s in seen.items() if len(s) >= min_companies}
    return common


def anonymise(text, code, common):
    t = re.sub(r"\s+", " ", text)
    t = re.sub(r"https?://\S+|www\.\S+|\S+@\S+", "[N]", t)
    t = re.sub(r"\b" + re.escape(code) + r"\b", "[N]", t)
    t = re.sub(r"\((?:ASX|TSX|AIM|NYSE|NASDAQ|OTCQB|OTCQX|FSE|JSE)[:\s][^)]*\)", "[N]", t, flags=re.I)   # (ASX: ABC), (TSX-V: XYZ)
    t = re.sub(r"\b[A-Z]{2,5}\b", lambda m: m.group(0) if m.group(0).lower() in common else "[N]", t)        # other tickers / acronyms
    t = re.sub(r"\b(19|20)\d{2}\b", "[Y]", t)
    t = re.sub(r"\b(FY|CY|H[12]|Q[1-4])\s?\d{2}\b", r"\1[Y]", t)
    t = re.sub(r"\b(FY|CY|H[12]|Q[1-4]|1H|2H)\s?(?:19|20)?\d{2}\b", r"\1[Y]", t)       # FY2021, CY21, 1H24
    t = re.sub(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b", "[D]", t)   # 31 January
    t = re.sub(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}\b", "[D]", t)
    t = PLACES.sub("[P]", t)                                                                     # head-office cities and states
    t = re.sub(r"\b(?:ABN|ACN|ARBN)\s*[\d ]{9,14}", "[N]", t)                                    # company numbers
    t = re.sub(r"\b(?:Level|Suite|Unit)\s+\d+[^.]{0,60}", "[A]", t)                               # street addresses
    t = CAP.sub(lambda m: m.group(0) if m.group(0).lower() in common else "[N]", t)
    t = re.sub(r"(\[N\][\s,]*){2,}", "[N] ", t)
    return t


# ---------- money ----------
def ledger():
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {"entries": [], "total": 0.0}


def spend(what, usage):
    L = ledger()
    usd = (usage.get("input_tokens", 0) * PRICE["in"] + usage.get("output_tokens", 0) * PRICE["out"]
           + usage.get("cache_read_input_tokens", 0) * PRICE["cache_read"]
           + usage.get("cache_creation_input_tokens", 0) * PRICE["cache_write"]) / 1e6
    L["entries"].append({"date": date.today().isoformat(), "what": what, "usd": round(usd, 4), **usage})
    L["total"] = round(sum(e["usd"] for e in L["entries"]), 4)
    AI.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(L, ensure_ascii=False, indent=1))
    return usd, L["total"]


# ---------- batch API ----------
def call(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def req(cid, user, system=None, max_tokens=400):
    # temperature is rejected by this model; thinking off + low effort keeps output short and cheap
    p = {"model": MODEL, "max_tokens": max_tokens, "thinking": {"type": "disabled"}, "output_config": {"effort": "low"},
         "messages": [{"role": "user", "content": user}]}
    if system:
        p["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    return {"custom_id": cid, "params": p}


def run_batch(requests, label, wait_min):
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    bid = st.get(label)
    if not bid:
        bid = json.loads(call(API, {"requests": requests}))["id"]
        st[label] = bid
        AI.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st))
        print("submitted", label, bid, len(requests))
    t0 = time.time()
    while True:
        b = json.loads(call(f"{API}/{bid}"))
        if b["processing_status"] == "ended":
            break
        if time.time() - t0 > wait_min * 60:
            print("batch still running; the next run picks it up:", bid, b.get("request_counts"))
            return None
        time.sleep(60)
    res = {}
    usage = Counter()
    per = defaultdict(Counter)
    errs, err_examples = Counter(), []
    for line in call(b["results_url"]).decode().splitlines():
        r = json.loads(line)
        if r["result"]["type"] != "succeeded":
            errs[r["result"]["type"]] += 1
            if len(err_examples) < 3:
                err_examples.append(json.dumps(r["result"])[:400])
        if r["result"]["type"] == "succeeded":
            m = r["result"]["message"]
            res[r["custom_id"]] = "".join(c.get("text", "") for c in m["content"])
            for k, v in m["usage"].items():
                if isinstance(v, int):
                    usage[k] += v
                    per[r["custom_id"][0]][k] += v
    print("batch results:", dict(b.get("request_counts", {})), dict(errs), *err_examples, sep="\n")
    st.pop(label, None)
    STATE.write_text(json.dumps(st))
    return res, dict(usage), {k: dict(v) for k, v in per.items()}


def parse_json(s):
    m = re.search(r"\{.*\}", s, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


# ---------- prompts ----------
def prior_of(x, by_code):
    """The company's previous price-sensitive announcement with a similar headline (first 1200 chars)."""
    words = set(re.findall(r"[a-z]{4,}", x["headline"].lower()))
    best = None
    for y in by_code[x["code"]]:
        if y["date"] >= x["date"]:
            break
        if words & set(re.findall(r"[a-z]{4,}", y["headline"].lower())):
            best = y
    return best


NOTES_SYSTEM = """You are an analyst reading one PART of a long ANONYMISED announcement from a small company listed on the
Australian stock exchange (names replaced by [N], years by [Y]). Write notes another analyst will use to judge the whole
announcement without seeing this part. Return ONLY one JSON object:
{"key_facts": [up to 8 short strings with the hard numbers: grades/widths, resource tonnes, revenue, cash, raise size and price,
 contract value, trial results, dates relative to now], "risks": [up to 5 short strings: dilution, debt, delays, failed results,
 going-concern words], "hype": 0-3, "pages": "page numbers these notes come from"}"""


def segments(text):
    """Cut at page markers into parts of at most SEG chars; a page longer than SEG is split."""
    pages = re.split(r"(?=\[page \d+\]|\[第\d+页\])", text) if re.search(r"\[page \d+\]|\[第\d+页\]", text) else [text]
    parts, cur = [], ""
    for pg in pages:
        while len(pg) > SEG:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(pg[:SEG])
            pg = pg[SEG:]
        if len(cur) + len(pg) > SEG and cur:
            parts.append(cur)
            cur = ""
        cur += pg
    if cur.strip():
        parts.append(cur)
    return parts or [""]


def prior_text(x, by_code, common):
    p = prior_of(x, by_code)
    return anonymise(p["headline"] + ". " + p["text"], p["code"], common)[:1200] if p else "none"


def first_requests(tag, x, by_code, common):
    """One scoring request for a short announcement, or one notes request per part of a long one."""
    parts = segments(anonymise(x["headline"] + ". " + x["text"], x["code"], common))
    if len(parts) == 1:
        return [req(tag, f"PRIOR (earlier announcement of the same kind):\n{prior_text(x, by_code, common)}\n\nANNOUNCEMENT:\n{parts[0]}", SYSTEM)], 1
    return [req(f"{tag}p{k}", f"PART {k + 1} of {len(parts)}:\n{p}", NOTES_SYSTEM, max_tokens=700) for k, p in enumerate(parts)], len(parts)


def combine_request(tag, x, n, notes, by_code, common):
    first = segments(anonymise(x["headline"] + ". " + x["text"], x["code"], common))[0][:8000]
    body = "\n".join(f"NOTES ON PART {k + 1}/{n}: {notes.get(k, '(missing)')}" for k in range(n))
    return req(f"c{tag[1:]}", f"PRIOR (earlier announcement of the same kind):\n{prior_text(x, by_code, common)}\n\n"
               f"ANNOUNCEMENT (long; first part in full, then an analyst's notes on every part):\n{first}\n\n{body}", SYSTEM)


def fwd21(code, d):
    rows = study.load(f"{code}.AU")
    ds = [r[0] for r in rows]
    import bisect
    i = bisect.bisect_left(ds, d)
    if i + 21 >= len(rows) or not rows[i][4]:
        return None
    return rows[i + 21][4] / rows[i][4] - 1


# ---------- stages ----------
def deanon_probe(all_docs):
    """Stage "deanon": only the "which company is this?" test, for several anonymiser strengths (about $0.01 per 200 docs)."""
    rnd = random.Random(761)
    probe = rnd.sample(all_docs, min(200, len(all_docs)))
    levels = [int(v) for v in os.environ.get("S76_ANON_LEVELS", "20,60,150").split(",")]
    commons = {lv: build_anonymiser(all_docs, lv) for lv in levels}
    reqs = []
    for lv in levels:
        reqs += [req(f"L{lv}x{i}", "Below is an anonymised excerpt of an ASX company announcement. Which ASX-listed company issued it? "
                     "Reply with only its ASX ticker, or UNKNOWN if you cannot tell.\n\n"
                     + anonymise(x["headline"] + ". " + x["text"], x["code"], commons[lv])[:3000], max_tokens=10)
                 for i, x in enumerate(probe)]
    got = run_batch(reqs, "deanon", float(os.environ.get("S76_WAIT", "150")))
    if got is None:
        return {"stage": "deanon", "status": "batch still running"}
    res, usage, per = got
    usd, total = spend(f"round 76 de-anonymisation probe (levels {levels})", usage)
    out = {"stage": "deanon", "usd": round(usd, 4), "ledger_total_usd": total, "levels": {}}
    for lv in levels:
        hits = sum(1 for i, x in enumerate(probe) if res.get(f"L{lv}x{i}", "").strip().upper().strip(".").startswith(x["code"]))
        sample = anonymise(probe[0]["headline"] + ". " + probe[0]["text"], probe[0]["code"], commons[lv])[:400]
        out["levels"][lv] = {"hit_pct": round(100 * hits / len(probe), 1), "kept_words": len(commons[lv]), "sample": sample}
    return out


def pilot(all_docs, common):
    rnd = random.Random(76)
    by_code = defaultdict(list)
    for x in all_docs:
        by_code[x["code"]].append(x)
    full = [x for x in all_docs if x["full"]]       # only docs stored in full give a true cost estimate
    if len(full) < 400:
        return {"stage": "pilot", "status": f"waiting: only {len(full)} price-sensitive docs stored in full text (need 400)"}
    pool = full
    sample = rnd.sample(pool, min(200, len(pool)))
    deanon = rnd.sample(all_docs, min(200, len(all_docs)))
    recall_pool = [x for x in all_docs if x["date"] < "2026-06"]
    recall = rnd.sample(recall_pool, min(300, len(recall_pool)))
    reqs, nparts = [], {}
    for i, x in enumerate(sample):
        r, nparts[i] = first_requests(f"s{i}", x, by_code, common)
        reqs += r
    reqs += [req(f"d{i}", "Below is an anonymised excerpt of an ASX company announcement. Which ASX-listed company issued it? "
                 "Reply with only its ASX ticker, or UNKNOWN if you cannot tell.\n\n"
                 + anonymise(x["headline"] + ". " + x["text"], x["code"], common)[:3000], max_tokens=10)
             for i, x in enumerate(deanon)]
    reqs += [req(f"r{i}", f"ASX ticker {x['code']}. Starting from {x['date']}, did its share price go up or down over the following "
                 "month? If you do not actually know, say UNKNOWN. Reply with one word: UP, DOWN or UNKNOWN.", max_tokens=5)
             for i, x in enumerate(recall)]
    got = run_batch(reqs, "pilot", float(os.environ.get("S76_WAIT", "150")))
    if got is None:
        return {"stage": "pilot", "status": "batch still running"}
    res, usage, per = got
    usd, total = spend("round 76 pilot (200 scored + 200 de-anon + 300 recall)", usage)
    long_ = [i for i in nparts if nparts[i] > 1]
    if long_:   # phase B: score the long ones from their part notes
        creqs = [combine_request(f"s{i}", sample[i], nparts[i], {k: res.get(f"s{i}p{k}", "") for k in range(nparts[i])}, by_code, common)
                 for i in long_]
        gotb = run_batch(creqs, "pilot_b", float(os.environ.get("S76_WAIT", "150")))
        if gotb is None:
            return {"stage": "pilot", "status": "phase B batch still running (phase A already paid and logged)"}
        resb, usageb, perb = gotb
        usd2, total = spend("round 76 pilot phase B (combine long announcements)", usageb)
        usd += usd2
        for i in long_:
            res[f"s{i}"] = resb.get(f"c{i}", "")
        per["c"] = perb.get("c", {})
        for k, v in usageb.items():
            usage[k] = usage.get(k, 0) + v
    # scoring: parse rate, cost per scored doc
    parsed = 0
    score_tokens = Counter()
    CACHE.mkdir(parents=True, exist_ok=True)
    examples = []
    for i, x in enumerate(sample):
        j = parse_json(res.get(f"s{i}", ""))
        if j:
            parsed += 1
            (CACHE / f"{x['id']}.json").write_text(json.dumps({"code": x["code"], "date": x["date"], **j}, ensure_ascii=False))
            if len(examples) < 5:
                examples.append({"headline": x["headline"][:90], **j})
    # de-anonymisation
    hits = sum(1 for i, x in enumerate(deanon) if res.get(f"d{i}", "").strip().upper().strip(".").startswith(x["code"]))
    # recall
    ans = Counter()
    right = n_ans = 0
    for i, x in enumerate(recall):
        a = res.get(f"r{i}", "").strip().upper()
        a = "UP" if a.startswith("UP") else "DOWN" if a.startswith("DOWN") else "UNKNOWN"
        ans[a] += 1
        if a != "UNKNOWN":
            r = fwd21(x["code"], x["date"])
            if r is not None and r != 0:
                n_ans += 1
                right += (r > 0) == (a == "UP")
    su = Counter(per.get("s", {})) + Counter(per.get("c", {}))
    s_usd = (su.get("input_tokens", 0) * PRICE["in"] + su.get("output_tokens", 0) * PRICE["out"]
             + su.get("cache_read_input_tokens", 0) * PRICE["cache_read"] + su.get("cache_creation_input_tokens", 0) * PRICE["cache_write"]) / 1e6
    per_doc = s_usd / max(1, len(sample))
    todo = [x for x in all_docs if not (CACHE / f"{x['id']}.json").exists()]
    est = per_doc * len(todo)
    out = {"stage": "pilot", "model": MODEL, "docs_sensitive": len(all_docs), "companies": len({x["code"] for x in all_docs}),
           "pre_cutoff": sum(x["date"] < CUTOFF for x in all_docs), "post_cutoff": sum(x["date"] >= CUTOFF for x in all_docs),
           "pilot_usd": round(usd, 4), "usd_per_scored_doc": round(per_doc, 6), "usage_by_kind": per, "ledger_total_usd": total, "usage": usage,
           "parsed": f"{parsed}/{len(sample)}", "sample_full_text": sum(x["full"] for x in sample),
           "sample_long_parts": {"docs": len(long_), "parts": sum(nparts[i] for i in long_)}, "docs_full_text": len(full), "est_full_usd": round(est, 2), "todo": len(todo),
           "deanon_hit_pct": round(100 * hits / len(deanon), 1),
           "recall": dict(ans), "recall_unknown_pct": round(100 * ans["UNKNOWN"] / max(1, sum(ans.values())), 1),
           "recall_answered_accuracy_pct": round(100 * right / n_ans, 1) if n_ans else None, "recall_answered_n": n_ans,
           "gates": {"deanon_ok": hits / len(deanon) <= 0.05,
                     "recall_ok": ans["UNKNOWN"] / max(1, sum(ans.values())) >= 0.8 and (n_ans < 20 or right / n_ans < 0.6)},
           "examples": examples}
    return out


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY missing")
    m = re.search(r"s76probe=(\S+)", Path(".github/run-quick").read_text()) if Path(".github/run-quick").exists() else None
    if m:   # free: read back an earlier batch's errors
        b = json.loads(call(f"{API}/{m.group(1)}"))
        print("probe", b.get("processing_status"), b.get("request_counts"))
        for line in call(b["results_url"]).decode().splitlines()[:3]:
            print(line[:600])
        return
    L = ledger()
    if L["total"] >= BUDGET_STOP:
        raise SystemExit(f"AI budget stop: ${L['total']} spent")
    all_docs = docs()
    print("price-sensitive docs", len(all_docs))
    vals = Counter()
    for f in sorted(ANN.glob("*.json.gz"))[:40]:
        for doc in json.loads(gzip.decompress(f.read_bytes()))["docs"].values():
            vals[repr(doc.get("sensitive"))] += 1
    print("sensitive flag values (first 40 companies):", dict(vals))
    if len(all_docs) < 2000:
        raise SystemExit("price-sensitive flags not corrected yet for enough companies; run again after the announcement job")
    common = build_anonymiser(all_docs)
    stage = os.environ.get("S76_STAGE", "pilot")
    done = AI / f"s76_{stage}_{MODEL}.json"   # a finished pilot is never re-run (and never paid twice)
    if stage == "deanon":
        dd = AI / f"s76_deanon_{os.environ.get('S76_ANON_LEVELS', '20,60,150').replace(',', '-')}.json"   # never paid twice
        if dd.exists():
            out = json.loads(dd.read_text(encoding="utf-8"))
        else:
            out = deanon_probe(all_docs)
            if "levels" in out:
                AI.mkdir(parents=True, exist_ok=True)
                dd.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    elif stage == "pilot" and done.exists():
        out = json.loads(done.read_text(encoding="utf-8"))
        print("[76] pilot already finished for", MODEL, "- reusing the saved result")
    else:
        out = pilot(all_docs, common) if stage == "pilot" else {"stage": stage, "status": "not built yet"}
        if stage == "pilot" and "pilot_usd" in out:
            done.parent.mkdir(parents=True, exist_ok=True)
            done.write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")
    out["ledger"] = ledger()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k not in ("examples", "ledger")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
