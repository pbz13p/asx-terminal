"""Round 87 prep: DeepSeek reads every 云聪 transcript in full and writes one study note per video (public YouTube content only).

Input: research/creators/云聪/transcripts.json.gz.enc (bundle sealed on Frank's PC with ASX_DATA_KEY).
Output: out/yc_notes.json {vid: {title, note, usage}} + spend, encrypted by the workflow and pushed to quick-results.
Cached per video in data/ycnotes (Actions cache): no video is paid for twice. Stops when spend passes YC_MAX_USD.
"""
import gzip
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import securedata

KEY = os.environ["DEEPSEEK_API_KEY"]
H = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"}
MODEL = os.environ.get("YC_MODEL", "deepseek-flash")
MAX_USD = float(os.environ.get("YC_MAX_USD", "3"))
SKIP = {"6u0fQJG0asc", "uac8lDkQJKQ", "AL0viualHwc", "x8Rebl1MOb4"}   # already read by Claude
CACHE = Path("data/ycnotes")
CACHE.mkdir(parents=True, exist_ok=True)

SYS = """你是量化研究员，在帮一位做澳股小盘和 A 股、也做加密合约的投资者，逐集深度学习 YouTube 频道"云聪金融交易分析"的视频。
下面给你一集视频的完整逐句文字稿（Whisper 自动转写，格式"[分:秒] 原文"，同音字很多，例如 实盘→石盘、涨停→涨厅、低吸→DC）。
必须从头读到尾，不能只看开头。用中文写一份笔记，严格按这个格式：

# <期号和标题>
## 一句话
<这集讲什么、核心观点>
## 要点（按时间）
- mm:ss 要点（尽量保留他的原话意思；重要的原话用「」括起来，每条不超过 25 字）
（8～20 条，覆盖整集）
## 方法和规则
- 写成可以执行的规则：进场条件、出场/止损条件、仓位、周期、适用市场。他说得模糊的就写"模糊："并写出最合理的量化版本。
## 提到的数字和案例
- 业绩、收益率、胜率、人物、年份、股票、品种（全部标"待核"）
## 能不能测
- 我们有的数据：澳股和 A 股全部日线（含退市股、2015 年起）、全部公告全文、A 股龙虎榜、币安全部 USDT 永续合约 1 小时 K 线和资金费率。没有：股票分钟线、盘口逐笔、期货。
- 每条规则写：能测/不能测，能测的写出具体测法（信号定义、持有期、对照组）；不能测的写缺什么。
## 同音字和听不清的地方
- 原文 → 应为（拿不准就写"？"）
不要寒暄，不要免责声明，不要编造文字稿里没有的内容。"""


def fmt(v):
    out = []
    for t, text in v["lines"]:
        out.append(f"[{int(t)//60:02d}:{int(t)%60:02d}] {text}")
    return "\n".join(out)


def post(path, body=None):
    req = urllib.request.Request("https://api.deepseek.com" + path, headers=H,
                                 data=json.dumps(body).encode() if body else None)
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)


def balance():
    try:
        b = post("/user/balance")
        return sum(float(x["total_balance"]) for x in b.get("balance_infos", []) if x.get("currency") == "USD")
    except Exception as e:
        print("balance failed", type(e).__name__)
        return None


def one(item):
    vid, v = item
    f = CACHE / f"{vid}.json"
    if f.exists():
        return vid, json.loads(f.read_text())
    user = f"标题：{v['title']}\n时长：{int(v.get('duration') or 0)//60} 分钟\n\n文字稿：\n{fmt(v)}"
    for attempt in range(4):
        try:
            r = post("/chat/completions", {"model": MODEL, "max_tokens": 16000, "temperature": 0.2,
                                           "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": user}]})
            note = r["choices"][0]["message"]["content"].strip()
            if len(note) < 300:
                raise ValueError("note too short")
            d = {"title": v["title"], "note": note, "usage": r.get("usage"), "model": MODEL}
            f.write_text(json.dumps(d, ensure_ascii=False))
            print("ok", vid, len(note), (r.get("usage") or {}).get("total_tokens"), flush=True)
            return vid, d
        except Exception as e:
            print("retry", vid, attempt, type(e).__name__, str(e)[:120], flush=True)
            time.sleep(20 * (attempt + 1))
    return vid, None


def main():
    bundle = json.loads(gzip.decompress(securedata.decrypt(Path("research/creators/云聪/transcripts.json.gz.enc").read_bytes())))
    todo = [(k, v) for k, v in bundle.items() if k not in SKIP and v.get("lines")]
    print("videos", len(bundle), "to read", len(todo), flush=True)
    b0 = balance()
    notes, spent = {}, 0.0
    with ThreadPoolExecutor(4) as ex:
        for i in range(0, len(todo), 8):
            for vid, d in ex.map(one, todo[i:i + 8]):
                if d:
                    notes[vid] = d
            b = balance()
            if b0 is not None and b is not None:
                spent = b0 - b
                if spent > MAX_USD:
                    print("spend cap reached", flush=True)
                    break
    tok = {"prompt": 0, "completion": 0}
    for d in notes.values():
        u = d.get("usage") or {}
        tok["prompt"] += u.get("prompt_tokens", 0)
        tok["completion"] += u.get("completion_tokens", 0)
    Path("out").mkdir(exist_ok=True)
    Path("out/yc_notes.json").write_text(json.dumps({"notes": notes, "videos": len(bundle), "read": len(notes),
                                                     "left": len(todo) - len(notes), "spent_usd_this_run": round(spent, 4),
                                                     "balance_usd": balance(), "tokens_cached_total": tok, "model": MODEL},
                                                    ensure_ascii=False))
    print("read", len(notes), "of", len(todo), "| spent this run (USD)", round(spent, 4), "| tokens", tok, flush=True)


if __name__ == "__main__":
    main()
