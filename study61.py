"""Round 61: 波动率学习 module (see volml.py). Writes STUDY61_OUT (default data/volml_results.json).
Afterwards the weekly live track (voltrack.py, out/vol_track.json) records this week's forecasts, scores past weeks
against what really happened, and the summary is put into the results file as "track" for the app."""
import json
import os
from pathlib import Path

import study
import volml
import voltrack

if __name__ == "__main__":
    volml.main()
    p = Path(os.environ.get("STUDY61_OUT", str(study.DATA / "volml_results.json")))
    try:
        out = json.loads(p.read_text(encoding="utf-8"))
        out["track"] = voltrack.run(out.get("latest") or [])
        p.write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")
        print("[track]", json.dumps(out["track"].get("last"), ensure_ascii=False), "pending", out["track"]["pending"])
    except Exception as e:  # never lose the forecasts because of the tracker
        print("[track] failed:", e)
