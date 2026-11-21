"""中文澳股小盘公告速读（Frank 2026-11-21：网站先在后台做好，只给他自己看，不要域名）.

Runs in quick-study.yml right after ann_lists.py (round 76 has just read the day's new price-sensitive announcements).
For every price-sensitive announcement of the last 7 days from a small, tradable company:
  - facts we already have: code, date, headline, round-76 type and numbers, price, traded value;
  - one Chinese plain-language sentence of FACTS ONLY, written by Haiku from the headline + the first part of the text.
    Cached per announcement id in data/ai/cnbrief/ (never paid twice); every call goes into the one AI ledger.
Legal line (ASIC INFO 269): this page states what the announcement says. No good/bad labels, no buy/sell words,
no round-96 "avoid / clean good news" lists -- those are our private research and stay off this page.
Output: out/cn_brief.json (encrypted to the quick-results branch by the workflow; the terminal serves it at /cnbrief.html).
"""
import gzip
import json
import os
import re
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import study
import study76

DAYS = int(os.environ.get("CNB_DAYS", "7"))
MAX_PRICE = float(os.environ.get("CNB_MAX_PRICE", "2.0"))     # "小盘股": share price under A$2 (simple first cut)
MIN_VALUE = 20_000                                             # same tradable filter as round 96 (20-session median A$)
CACHE = study.DATA / "ai" / "cnbrief"
OUT = Path(os.environ.get("CNB_OUT", "out/cn_brief.json"))
MODEL = os.environ.get("CNB_MODEL", "claude-haiku-5-5")
PRICE = {"in": 0.10, "out": 0.50}                               # USD per MTok, normal (not batch) API = 2x the batch price
STOP_LEFT = 2.0                                                # stop paying when the ledger is within $2 of the total budget
TYPES = {"drilling_assay": "钻探/化验", "resource_reserve": "资源量", "capital_raising": "融资",
                         "quarterly_report": "季报", "financial_results": "业绩", "contract_order": "合同/订单",
                         "acquisition_divestment": "收购/出售", "permit_approval": "许可/批准", "clinical_regulatory": "临床/监管",
                         "takeover_scheme": "收购要约", "production_update": "生产", "other": "其他"}

PROMPT = """下面是一家澳洲上市小公司的价格敏感公告（标题加正文开头）。用简体中文写一到两句大白话，只写公告里的事实：
发生了什么、关键数字（金额、价格、折让、股数、品位、期限、现金）、有没有附带条件或还没落实的部分。
规则：
- 只写事实，不评价好坏，不用“利好”“利空”“值得关注”“大涨”“前景”这类词，不预测股价，不给任何买卖建议。
- 数字照原文，单位写清楚（澳元/美元，百万＝million，亿＝100 million；例如 A$50m＝5000 万澳元）。原文没有的数字不要补。
- 公司名、项目名保留英文原名，不要翻译。
- 不超过 70 个汉字。只输出这一两句话，不加引号、标题或说明。

标题：{headline}
正文开头：
{text}"""


def ledger_total():
    try:
        return study76.ledger()["total"]
    except Exception:
        return 0.0


def spend(usage):
    usd = (usage.get("input_tokens", 0) * PRICE["in"] + usage.get("output_tokens", 0) * PRICE["out"]) / 1e6
    L = study76.ledger()
    L["entries"].append({"date": date.today().isoformat(), "what": "cn_brief 中文公告速读", "usd": round(usd, 5), **usage})
    L["total"] = round(sum(e["usd"] for e in L["entries"]), 4)
    study76.LEDGER.parent.mkdir(parents=True, exist_ok=True)
    study76.LEDGER.write_text(json.dumps(L, ensure_ascii=False, indent=1))


GEMINI = os.environ.get("GEMINI_API_KEY", "")
GEM_MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest"]   # big one is often busy: fall back to the lite one


def ask_gemini(prompt):
    """Free Gemini tier first (CLAUDE.md: public content, coarse work -> free models first). Returns '' on failure."""
    if not GEMINI:
        return ""
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": 400}}).encode()
    for m in GEM_MODELS:
        for attempt in range(2):
            try:
                req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
                                             data=body, headers={"content-type": "application/json", "x-goog-api-key": GEMINI},
                                             method="POST")
                with urllib.request.urlopen(req, timeout=90) as r:
                    j = json.loads(r.read())
                parts = (j.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
                out = "".join(p.get("text", "") for p in parts).strip()
                if out:
                    return out
            except Exception:  # noqa: BLE001
                time.sleep(4 * (attempt + 1))
    return ""


def ask(headline, text):
    """Chinese facts-only line: Gemini (free) first, Haiku (paid, ledger) only if Gemini fails. Returns (text, usage, model)."""
    prompt = PROMPT.format(headline=headline, text=text[:3500])
    out = ask_gemini(prompt)
    if out:
        return out, {}, "gemini"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "", {}, ""
    body = {"model": MODEL, "max_tokens": 300, "thinking": {"type": "disabled"}, "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                 headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"}, method="POST")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                j = json.loads(r.read())
            out = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text").strip()
            return out, j.get("usage", {}), "haiku"
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                print("[cnb] call failed:", type(e).__name__, str(e)[:120])
            time.sleep(3 * (attempt + 1))
    return "", {}, ""


def recent_docs(since):
    """Price-sensitive announcements since `since` from the round 68 archive: id -> dict."""
    out = {}
    for f in sorted(study76.ANN.glob("*.json.gz")):
        try:
            d = json.loads(gzip.decompress(f.read_bytes()))
        except Exception:
            continue
        for ids, doc in d.get("docs", {}).items():
            if doc.get("sensitive") and doc.get("date", "")[:10] >= since:
                out[ids] = {"id": ids, "code": d["code"], "date": doc["date"][:10], "headline": doc.get("headline", ""),
                            "text": doc.get("text") or ""}
    return out


def names():
    try:
        rows = study.api("exchange-symbol-list/AU")
        return {r["Code"]: r.get("Name", "") for r in rows}
    except Exception as e:  # noqa: BLE001
        print("[cnb] no company names:", type(e).__name__)
        return {}


def main():
    since = (date.today() - timedelta(days=DAYS)).isoformat()
    docs = recent_docs(since)
    print("[cnb] price-sensitive announcements since", since, ":", len(docs))
    nm = names()
    items, todo = [], []
    CACHE.mkdir(parents=True, exist_ok=True)
    for x in sorted(docs.values(), key=lambda x: (x["date"], x["code"]), reverse=True):
        rows = study.load(f"{x['code']}.AU")
        if len(rows) < 25:
            continue
        px = rows[-1][6] or rows[-1][4]
        if not px or px >= MAX_PRICE:
            continue
        val = statistics.median(r[6] * r[5] for r in rows[-20:] if r[5])
        if val < MIN_VALUE:
            continue
        s = {}
        sf = study76.CACHE / f"{x['id']}.json"
        if sf.exists():
            try:
                s = json.loads(sf.read_text(encoding="utf-8"))
            except Exception:
                s = {}
        it = {"id": x["id"], "code": x["code"], "name": nm.get(x["code"], ""), "date": x["date"], "headline": x["headline"][:160],
              "type": TYPES.get(s.get("type"), ""), "price": round(px, 4), "value_k": round(val / 1000),
              "numbers": {k: v for k, v in (s.get("numbers") or {}).items() if v}, "cash_q": s.get("cash_quarters")}
        cf = CACHE / f"{x['id']}.json"
        if cf.exists():
            it["zh"] = json.loads(cf.read_text(encoding="utf-8")).get("zh", "")
        elif x["text"]:
            todo.append((it, x))
        items.append(it)
    budget = getattr(study76, "BUDGET_STOP", 0) or 0
    paid_ok = not budget or ledger_total() < budget - STOP_LEFT
    if not paid_ok:
        os.environ.pop("ANTHROPIC_API_KEY", None)          # budget nearly used: free model only
    if todo and (GEMINI or os.environ.get("ANTHROPIC_API_KEY")):
        print("[cnb] new announcements to summarise:", len(todo))

        def one(pair):
            it, x = pair
            zh, usage, m = ask(x["headline"], x["text"])
            return it, zh, usage, m

        with ThreadPoolExecutor(4) as ex:
            used = {}
            for it, zh, usage, m in ex.map(one, todo):
                used[m] = used.get(m, 0) + 1
                if usage:
                    spend(usage)
                zh = re.sub(r"\s+", " ", zh).strip().strip("“”\"")
                if zh:
                    it["zh"] = zh
                    (CACHE / f"{it['id']}.json").write_text(json.dumps({"zh": zh, "model": m, "at": time.strftime("%F")},
                                                                       ensure_ascii=False), encoding="utf-8")
            print("[cnb] written by model:", used)
    elif todo:
        print("[cnb] not summarising", len(todo), "new ones: no model key available")
    out = {"generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "since": since, "max_price": MAX_PRICE,
           "items": items,
           "source": "ASX 上市公司价格敏感公告原文（asx.com.au）；中文摘要由 AI 根据公告生成，以原公告为准。",
           "note": "只摘录公告中的事实，不评价好坏，不构成任何买卖建议。"}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("[cnb] written:", len(items), "items,", sum(1 for i in items if i.get("zh")), "with Chinese")


if __name__ == "__main__":
    main()
