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
MODEL = os.environ.get("TRANSLATE_MODEL", "claude-haiku-5-5")


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


def translate(code, ids):
    f = CACHE / f"{ids}.zh.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        d["cached"] = True
        return d
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("没有设置 AI 翻译密钥：可以用浏览器右键“翻译成中文”免费翻译")
    src = text(code, ids)
    body = src["text"][:30000]
    prompt = ("把下面这份澳洲上市公司公告完整翻译成简体中文。保留数字、金额、日期、表格结构和公司名；专业词用中国投资者熟悉的说法"
              "（如 placement=定向增发/配售，Appendix 5B=季度现金流报告，trading halt=停牌）。只输出译文。\n\n标题：" + src["headline"] + "\n\n" + body)
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", method="POST",
                                 data=json.dumps({"model": MODEL, "max_tokens": 16000,
                                                  "messages": [{"role": "user", "content": prompt}]}).encode(),
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        res = json.loads(r.read())
    zh = "".join(b.get("text", "") for b in res.get("content", []))
    u = res.get("usage", {})
    out = {"ids": ids, "headline": src["headline"], "zh": zh, "tokens_in": u.get("input_tokens"), "tokens_out": u.get("output_tokens"),
           "model": MODEL, "truncated": len(src["text"]) > 30000}
    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    log = CACHE / "usage.jsonl"
    with log.open("a", encoding="utf-8") as g:
        g.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M"), "ids": ids, "in": out["tokens_in"], "out": out["tokens_out"]}) + "\n")
    out["cached"] = False
    return out
