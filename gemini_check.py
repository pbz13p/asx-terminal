"""One-off check that the GEMINI_API_KEY secret works (free tier). Prints model names and OK/error only, never the key."""
import json
import os
import urllib.error
import urllib.request

KEY = os.environ.get("GEMINI_API_KEY", "")
BASE = "https://generativelanguage.googleapis.com/v1beta"


def call(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body else None,
                                 headers={"x-goog-api-key": KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=150) as r:
        return json.load(r)


if not KEY:
    raise SystemExit("GEMINI_API_KEY is empty")
try:
    models = [m["name"] for m in call("/models?pageSize=200").get("models", [])
              if "generateContent" in m.get("supportedGenerationMethods", [])]
    print("models:", len(models))
    flash = [m for m in models if "flash" in m and "image" not in m and "tts" not in m and "live" not in m]
    print("flash models:", flash[:15])
    tried = []
    for pick in ["models/gemini-3.8-flash", "models/gemini-flash-latest", "models/gemini-3.5-flash", "models/gemini-flash-lite-latest"]:
        if pick not in models:
            continue
        try:
            out = call(f"/{pick}:generateContent", {"contents": [{"parts": [{"text": "用一句中文回答：1+1等于几？"}]}]})
            text = out["candidates"][0]["content"]["parts"][0]["text"]
            print("test model:", pick, "| reply:", text.strip()[:60], "| usage:", out.get("usageMetadata"))
            break
        except Exception as e:   # busy (503), retired (404) or slow: try the next one
            tried.append((pick, getattr(e, "code", type(e).__name__)))
    print("tried:", tried)
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read()[:300])
    raise SystemExit(1)
