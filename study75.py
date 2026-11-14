"""Probe: are these domain names still free? (RDAP at the official registries; 404 = not registered)."""
import json, os, urllib.request, urllib.error
NAMES = os.environ.get("DOMAINS", "terminal.com,neonterminal.com,howmai.com,howmai.net,howmai.app,neon-terminal.com,neonterm.net,pengneon.com").split(",")
RDAP = {"com": "https://rdap.verisign.com/com/v1/domain/", "net": "https://rdap.verisign.com/net/v1/domain/", "app": "https://pubapi.registry.google/rdap/domain/"}
out = {}
for d in NAMES:
    tld = d.rsplit(".", 1)[1]
    try:
        with urllib.request.urlopen(RDAP[tld] + d, timeout=20) as r:
            j = json.loads(r.read())
            ev = {e.get("eventAction"): e.get("eventDate", "")[:10] for e in j.get("events", [])}
            out[d] = "已被注册（" + ev.get("registration", "?") + " 注册，" + ev.get("expiration", "?") + " 到期）"
    except urllib.error.HTTPError as e:
        out[d] = "可以注册" if e.code == 404 else f"查询出错 {e.code}"
    except Exception as e:
        out[d] = "查询失败 " + type(e).__name__
print(json.dumps(out, ensure_ascii=False, indent=1))
open(os.environ.get("STUDY75_OUT", "study75_results.json"), "w").write(json.dumps(out, ensure_ascii=False))
