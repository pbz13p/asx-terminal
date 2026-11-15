"""'问 AI' inside Neon Terminal: the user types an idea or question; the answer is grounded in our own research
(research centre findings, live track record, today's picks with reasons, the stock on screen).

Which AI each user gets is set in .env, because Anthropic does not support use from mainland China:
  SITE_AI=frank:claude,baba:deepseek        (local use on your own PC = claude)
  claude   -> ANTHROPIC_API_KEY  (Australia / supported regions only)
  deepseek -> DEEPSEEK_API_KEY   (api.deepseek.com, legal in China)
  qwen     -> QWEN_API_KEY       (Alibaba DashScope international, OpenAI-compatible)
Chats are kept in data/chat/<user>.jsonl; ideas sent for review go to data/chat/ideas.jsonl.
"""
import json
import os
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIR = ROOT / "data" / "chat"

SYSTEM = """你是 Neon Terminal 里的研究助手，用简体中文、大白话回答。用户是个人投资者（澳股或 A 股），会提出想法或问题。
规则：
1. 只根据下面"我们的资料"里的数据和结论判断，资料里没有的就直说"我们还没测过"，不要编数字。
2. 先一句话给结论：这个想法和我们的数据"一致 / 部分一致 / 相反 / 还没测过"。
3. 再说理由（引用资料里的具体数字，比如回测结果、实盘记录、选股名单里的"为什么"）。
4. 再说风险和要注意的地方。
5. 最后给"怎么验证"：如果值得正式回测，用一两句话写清楚要测的规则（买什么、什么时候买、拿多久、和什么比）。
6. 简洁，不超过 300 字。不要加"不构成投资建议"之类的话。"""


def provider_for(user):
    m = {}
    for part in os.environ.get("SITE_AI", "").split(","):
        if ":" in part:
            u, p = part.split(":", 1)
            m[u.strip()] = p.strip().lower()
    return m.get(user, "claude" if user == "local" else "deepseek")


def _post(url, body, headers, timeout=120):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"content-type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ask_model(provider, system, messages):
    if provider == "claude":
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ValueError("还没有设置 ANTHROPIC_API_KEY")
        res = _post("https://api.anthropic.com/v1/messages",
                    {"model": os.environ.get("CHAT_MODEL", "claude-sonnet-5-5"), "max_tokens": 1500, "system": system, "messages": messages},
                    {"x-api-key": key, "anthropic-version": "2023-06-01"})
        u = res.get("usage", {})
        return "".join(b.get("text", "") for b in res.get("content", [])), u.get("input_tokens", 0), u.get("output_tokens", 0)
    if provider in ("deepseek", "qwen"):
        key = os.environ.get("DEEPSEEK_API_KEY" if provider == "deepseek" else "QWEN_API_KEY", "")
        if not key:
            raise ValueError(("DEEPSEEK" if provider == "deepseek" else "QWEN") + "_API_KEY 还没有设置")
        url = ("https://api.deepseek.com/chat/completions" if provider == "deepseek"
               else "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions")
        model = "deepseek-chat" if provider == "deepseek" else "qwen-plus"
        res = _post(url, {"model": model, "max_tokens": 1500, "messages": [{"role": "system", "content": system}] + messages},
                    {"Authorization": "Bearer " + key})
        u = res.get("usage", {})
        return res["choices"][0]["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    raise ValueError("未知的 AI：" + provider)


def context(handler, market, code):
    """Our material, kept short: findings, latest news, track record, today's picks with reasons, the stock on screen."""
    parts = []
    try:
        r = handler.research()
        mk = "A股" if market == "cn" else "ASX"
        cards = [c for c in r.get("cards", []) if c.get("market") == mk]
        parts.append("【研究中心结论】\n" + "\n".join(
            f"- {c['title']}（{c['verdict']}，第{c.get('round')}轮）：{c['rule']} 怎么用：{c['use']} 数字：" +
            "；".join(f"{a} {b}" for a, b in c.get("numbers", [])) for c in cards))
        tk = (r.get("track") or {}).get(market)
        if tk:
            parts.append("【实盘记录】" + json.dumps(tk.get("lists"), ensure_ascii=False))
        parts.append("【最新更新】\n" + "\n".join(f"- {n['date']} {n['text']}" for n in (r.get("news") or [])[:6]))
    except Exception:
        pass
    if market == "cn":
        try:
            from server import fetch_result
            p = fetch_result("cn_picks.json")
            fmt = lambda x: f"{x['code']} {x.get('name','')} 20天{x.get('ret20')}% 原因：" + "；".join(w['f'] + w['v'] for w in x.get("why", []))
            parts.append(f"【今日 A 股模型选股（{p.get('date')}）前 20 名】\n" + "\n".join(fmt(x) for x in p.get("top", [])[:20]))
            parts.append("【今日要避开的前 10 名】\n" + "\n".join(fmt(x) for x in p.get("avoid", [])[:10]))
            parts.append("【模型最看重的因素】" + "，".join(f"{d['f']} {d['share']}%" for d in p.get("drivers", [])))
        except Exception:
            pass
    else:
        try:
            s = handler.route("scan", {})
            parts.append("【今日爆拉候选】" + "；".join(f"{x['code']} 离3年高点{x.get('from_high_3y')}% 放量{x.get('value_x')}倍" for x in (s.get("rockets") or [])[:15]))
        except Exception:
            pass
    if code:
        try:
            ins = handler.route("insight", {"code": code, "m": market})
            parts.append(f"【用户正在看的股票 {code} {ins.get('name','')}】现价 {ins.get('last')}，涨跌 {json.dumps(ins.get('chg'), ensure_ascii=False)}，"
                         f"离一年高点 {ins.get('dist_hi')}%，50日线 {ins.get('ma50')}，200日线 {ins.get('ma200')}，RSI {ins.get('rsi')}，20日波动 {ins.get('rv20')}%")
            prof = handler.route("profile", {"code": code, "m": market})
            if prof.get("lists"):
                parts.append("它在今天的名单里：" + json.dumps(prof["lists"], ensure_ascii=False)[:800])
            anns = handler.route("ann", {"code": code, "m": market})[:10]
            parts.append("最近公告标题：" + "；".join(f"{a['date']} {a['headline']}" for a in anns))
        except Exception:
            pass
    return "\n\n".join(parts)[:14000]


def history(user, n=40):
    f = DIR / f"{user}.jsonl"
    if not f.exists():
        return []
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()[-n:] if l.strip()]


def chat(handler, user, text, market, code):
    DIR.mkdir(parents=True, exist_ok=True)
    text = (text or "").strip()[:2000]
    if not text:
        raise ValueError("请输入问题")
    prov = provider_for(user)
    past = [m for m in history(user, 12) if m.get("market") == market][-6:]
    msgs = []
    for m in past:
        msgs += [{"role": "user", "content": m["q"]}, {"role": "assistant", "content": m["a"]}]
    msgs.append({"role": "user", "content": text})
    system = SYSTEM + "\n\n我们的资料：\n" + context(handler, market, code)
    a, tin, tout = ask_model(prov, system, msgs)
    rec = {"t": time.strftime("%Y-%m-%d %H:%M"), "id": str(int(time.time() * 1000)), "user": user, "market": market, "code": code,
           "q": text, "a": a, "ai": prov, "tokens_in": tin, "tokens_out": tout}
    with (DIR / f"{user}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def send_idea(user, msg_id, note=""):
    rec = next((m for m in history(user, 500) if m.get("id") == msg_id), None)
    if not rec:
        raise ValueError("找不到这条对话")
    rec = {**rec, "note": (note or "")[:500], "sent": time.strftime("%Y-%m-%d %H:%M"), "status": "待审核"}
    with (DIR / "ideas.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True}


def ideas():
    f = DIR / "ideas.jsonl"
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()][::-1] if f.exists() else []
