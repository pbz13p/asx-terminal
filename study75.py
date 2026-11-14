"""Probe: what is howmai.com.au pointing at now (DNS, nameservers, web page)?"""
import json, os, socket, subprocess, urllib.request
out = {}
for h in ("howmai.com.au", "www.howmai.com.au"):
    try:
        out[h + " A"] = sorted({a[4][0] for a in socket.getaddrinfo(h, 443)})
    except Exception as e:
        out[h + " A"] = type(e).__name__
try:
    out["NS"] = subprocess.run(["dig", "+short", "NS", "howmai.com.au"], capture_output=True, text=True, timeout=20).stdout.split()
    out["whois"] = [l for l in subprocess.run(["whois", "howmai.com.au"], capture_output=True, text=True, timeout=30).stdout.splitlines()
                    if any(k in l for k in ("Registrar Name", "Status", "Name Server", "Last Modified", "Registrant:"))][:12]
except Exception as e:
    out["dig"] = repr(e)
for u in ("https://howmai.com.au", "http://howmai.com.au"):
    try:
        with urllib.request.urlopen(u, timeout=20) as r:
            body = r.read(3000).decode("utf-8", "replace")
            import re
            t = re.search(r"<title>(.*?)</title>", body, re.S)
            out[u] = {"status": r.status, "final": r.geturl(), "title": t.group(1).strip()[:100] if t else None, "server": r.headers.get("server")}
    except Exception as e:
        out[u] = type(e).__name__ + " " + str(e)[:80]
print(json.dumps(out, ensure_ascii=False, indent=1))
open(os.environ.get("STUDY75_OUT", "study75_results.json"), "w").write(json.dumps(out, ensure_ascii=False))
