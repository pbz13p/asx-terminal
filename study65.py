"""Round 65: robustness of round 64's simple score in small caps -- higher costs (1% per unit of turnover,
closer to small-cap spreads) and a stricter liquidity floor ($200k/day)."""
import os
import sys

import study63

if __name__ == "__main__":
    import json, pathlib
    out = {}
    for name, env in (("成本 1% · 成交 ≥$5 万", {"S63_COST": "0.01", "S63_MINVAL": "50000"}),
                      ("成本 0.5% · 成交 ≥$20 万", {"S63_COST": "0.005", "S63_MINVAL": "200000"}),
                      ("成本 1% · 成交 ≥$20 万", {"S63_COST": "0.01", "S63_MINVAL": "200000"})):
        os.environ.update({"S63_N": "5000", "S63_PRICE": "0.05", "STUDY63_OUT": f"/tmp/s65_{len(out)}.json", **env})
        import importlib
        m = importlib.reload(study63)
        m.main()
        out[name] = json.loads(pathlib.Path(os.environ["STUDY63_OUT"]).read_text(encoding="utf-8"))
        out[name].pop("method", None)
    p = pathlib.Path(os.environ.get("STUDY65_OUT", "data/study65_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")
