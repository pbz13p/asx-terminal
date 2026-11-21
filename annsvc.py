"""Announcements for the terminal: the latest ASX announcements of one stock, the full text of one, and an optional
Chinese translation. Translation uses the Anthropic API only if ANTHROPIC_API_KEY is in .env, and every translation is
cached on disk (data/ann_cn/) so the same announcement is never paid for twice. Without a key the page shows the
English text in a page Chrome can translate for free.
"""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

import study67

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "ann_cn"
_mem = {}
MODEL = os.environ.get("TRANSLATE_MODEL", "claude-sonnet-5-5")  # accuracy first; chunks run in parallel for speed


def _cached(key, ttl, fn):
    hit = _mem.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _mem[key] = (time.time(), val)
    return val


def listing(code):
    def load():
        y = date.today().year
        before = study67.calls["failed"]
        items = study67.listing(code, [y, y - 1])
        if not items and study67.calls["failed"] > before:
            raise ValueError("ASX 网站暂时连不上")
        items.sort(key=lambda i: i["date"], reverse=True)
        return [{"date": i["date"], "headline": i["headline"], "sensitive": i["sensitive"], "ids": i["ids"]} for i in items[:40]]
    return _cached(f"list:{code}", 1800, load)


def _pypdf():
    try:
        import pypdf  # noqa: F401
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--user", "pypdf"], check=False)
    from pypdf import PdfReader
    return PdfReader


def text(code, ids):
    if not re.fullmatch(r"\d{4,12}", ids or ""):
        raise ValueError("公告编号不正确")
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{ids}.en.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    item = next((i for i in study67.listing(code, [date.today().year, date.today().year - 1]) if i["ids"] == ids), None)
    if not item:
        raise ValueError("找不到这条公告")
    url = "https://www.asx.com.au" + item["href"] if item["href"].startswith("/") else item["href"]
    body, final, ctype = study67.get(url)
    if "pdf" not in ctype:
        page = body.decode("utf-8", "replace")
        m = re.search(r'name="pdfURL"\s+value="([^"]+)"', page) or re.search(r'(https?://[^"\']+\.pdf)', page)
        if not m:
            raise ValueError("没找到 PDF")
        final = m.group(1)
        body, final, ctype = study67.get(final, 60)
    reader = _pypdf()(io.BytesIO(body))
    pages = [re.sub(r"[ \t]+", " ", p.extract_text() or "").strip() for p in reader.pages[:40]]
    out = {"ids": ids, "code": code, "date": item["date"], "headline": item["headline"], "pdf": final,
           "pages": len(reader.pages), "text": "\n\n".join(pages)[:60000]}
    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


GLOSSARY = ("placement=配售（定向增发）; entitlement offer / rights issue=配股; SPP (share purchase plan)=股东认购计划; "
            "Appendix 5B=季度现金流报告（附表 5B）; Appendix 4C=季度现金流报告（附表 4C）; trading halt=停牌; voluntary suspension=自愿停牌; "
            "reinstatement to quotation=复牌; scheme of arrangement=协议安排（收购方案）; takeover bid=收购要约; "
            "JORC=JORC 标准; Mineral Resource=矿产资源量; Ore Reserve=矿石储量; Indicated / Inferred / Measured=控制的 / 推断的 / 探明的; "
            "drill hole / intercept=钻孔 / 见矿段; g/t=克/吨; capital raising=融资; cornerstone investor=基石投资者; "
            "Director's interest notice / Appendix 3Y=董事持股变动通知（附表 3Y）; Cleansing notice=合规通知; "
            "quarters of funding available=现金可支撑的季度数; net cash used in operating activities=经营活动净现金流出; "
            "offtake agreement=包销协议; binding / non-binding=有约束力 / 无约束力; MOU=谅解备忘录; Board=董事会; AGM=年度股东大会")


def _call(key, prompt, max_tokens):
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", method="POST",
                                 data=json.dumps({"model": MODEL, "max_tokens": max_tokens, "temperature": 0,
                                                  "messages": [{"role": "user", "content": prompt}]}).encode(),
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as r:
        res = json.loads(r.read())
    u = res.get("usage", {})
    return "".join(b.get("text", "") for b in res.get("content", [])), u.get("input_tokens", 0), u.get("output_tokens", 0)


def _chunks(text, size=7000):
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if len(cur) + len(para) > size and cur:
            parts.append(cur)
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        parts.append(cur)
    return parts or [text]


def translate(code, ids):
    f = CACHE / f"{ids}.zh.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        d["cached"] = True
        return d
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("还没有设置 AI 翻译密钥（在 App 文件夹的 .env 里加一行 ANTHROPIC_API_KEY=…，再重启 App）")
    src = text(code, ids)
    parts = _chunks(src["text"][:80000])
    head = ("你是专业的澳洲证券市场翻译。把下面这段澳洲上市公司（ASX）公告逐句完整翻译成简体中文，要求：\n"
            "1. 准确第一：不漏译、不增译、不总结，每一句都要译；数字、金额（保留 A$/US$）、百分比、日期、单位、股票代码、人名、公司名一律照原样保留。\n"
            "2. 用中国投资者熟悉的专业说法，术语对照：" + GLOSSARY + "\n"
            "3. 表格按原来的行列译成每行一条，列之间用 | 分隔。\n"
            "4. 只输出译文，不要任何解释。\n\n公告标题：" + src["headline"] + "\n\n")
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(min(8, len(parts))) as pool:
        res = list(pool.map(lambda t: _call(key, head + "这是全文的第 {}/{} 段：\n\n".format(t[0] + 1, len(parts)) + t[1], 8000),
                            list(enumerate(parts))))
    zh = "\n\n".join(r[0].strip() for r in res)
    out = {"ids": ids, "headline": src["headline"], "zh": zh, "tokens_in": sum(r[1] for r in res), "tokens_out": sum(r[2] for r in res),
           "model": MODEL, "parts": len(parts), "truncated": len(src["text"]) > 80000}
    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    with (CACHE / "usage.jsonl").open("a", encoding="utf-8") as g:
        g.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M"), "ids": ids, "model": MODEL, "in": out["tokens_in"], "out": out["tokens_out"]}) + "\n")
    out["cached"] = False
    return out


BRIEF_MODEL = os.environ.get("BRIEF_MODEL", "claude-haiku-5-5")


def brief(code, ids):
    """One-line Chinese facts-only summary of one announcement (same rules as the nightly 中文速读), cached per id.
    Used by /cnbrief.html for a searched stock's older price-sensitive announcements (Frank 2026-11-21)."""
    f = CACHE / f"{ids}.brief.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("还没有设置 AI 密钥")
    from cnb_prompt import PROMPT, clean
    src = text(code, ids)
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", method="POST",
                                 data=json.dumps({"model": BRIEF_MODEL, "max_tokens": 600,
                                                  "messages": [{"role": "user", "content": PROMPT.format(headline=src["headline"], text=src["text"][:3500])}]}).encode(),
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.loads(r.read())
    u = res.get("usage", {})
    zh = clean("".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text"))
    out = {"ids": ids, "code": code, "date": src["date"], "headline": src["headline"], "zh": zh, "model": BRIEF_MODEL}
    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    with (CACHE / "usage.jsonl").open("a", encoding="utf-8") as g:
        g.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M"), "ids": ids, "model": BRIEF_MODEL, "what": "brief",
                            "in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0)}) + "\n")
    return out
