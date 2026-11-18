"""One-off check that the DEEPSEEK_API_KEY secret works. Prints balance, model and reply only, never the key."""
import json
import os
import urllib.request

KEY = os.environ.get("DEEPSEEK_API_KEY", "")
H = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"}
if not KEY:
    raise SystemExit("DEEPSEEK_API_KEY is empty")
with urllib.request.urlopen(urllib.request.Request("https://api.deepseek.com/user/balance", headers=H), timeout=60) as r:
    print("balance:", json.load(r))
with urllib.request.urlopen(urllib.request.Request("https://api.deepseek.com/models", headers=H), timeout=60) as r:
    print("models:", [m["id"] for m in json.load(r).get("data", [])])
body = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "用一句中文回答：1+1等于几？"}], "max_tokens": 30}
with urllib.request.urlopen(urllib.request.Request("https://api.deepseek.com/chat/completions", data=json.dumps(body).encode(), headers=H), timeout=120) as r:
    out = json.load(r)
print("reply:", out["choices"][0]["message"]["content"].strip()[:60], "| usage:", out.get("usage"))
