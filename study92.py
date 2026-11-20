"""Round 92 (helper): pull one company's announcements (full text) from our archive for reading, encrypted in transit.
S92_CODE (default CND), S92_FROM (default 2025-10-01). Output: out/study92_results.json {code, docs: [{date, headline, sensitive, text}]}.
"""
import gzip
import json
import os
from pathlib import Path

code = os.environ.get("S92_CODE", "CND")
since = os.environ.get("S92_FROM", "2025-10-01")
d = json.loads(gzip.decompress(Path(f"data/ann/{code}.json.gz").read_bytes()))
docs = [{"date": v.get("date", "")[:10], "headline": v.get("headline"), "sensitive": v.get("sensitive"), "text": v.get("text", "")}
        for v in d.get("docs", {}).values() if v.get("date", "") >= since and "error" not in v]
docs.sort(key=lambda x: x["date"], reverse=True)
Path("out").mkdir(exist_ok=True)
Path("out/study92_results.json").write_text(json.dumps({"code": code, "docs": docs}, ensure_ascii=False))
print("[92] done", flush=True)
