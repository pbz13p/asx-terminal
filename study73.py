"""Round 73 (probe): can GitHub's servers reach cninfo? One stock list request + one announcement query."""
import json, os, time
import cn_ann
out = {}
t = time.time()
try:
    o = cn_ann.org_ids(); out["orgids"] = len(o); out["sample"] = list(o.items())[:2]
except Exception as e:
    out["orgids_error"] = repr(e)
try:
    items = cn_ann.one("000001", "gssz0000001", time.strftime("%Y-%m-%d"))
    out["items_000001"] = len(items); out["first"] = items[:2]
except Exception as e:
    out["query_error"] = repr(e)
out["seconds"] = round(time.time() - t, 1); out["stats"] = cn_ann.stats
print(json.dumps(out, ensure_ascii=False))
open(os.environ.get("STUDY73_OUT", "study73_results.json"), "w").write(json.dumps(out, ensure_ascii=False))
