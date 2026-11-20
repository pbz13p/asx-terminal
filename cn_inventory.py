"""One-off check: does the A-share archive start at each stock's first trading day? Prints counts and dates only."""
import gzip
import json
from collections import Counter
from pathlib import Path

U = json.loads(Path("data/cn/universe.json").read_text())
files = {f.name.split(".")[0]: f for f in Path("data/cn").glob("*.json.gz")}
first, last, empty = {}, {}, 0
for code, f in files.items():
    rows = json.loads(gzip.decompress(f.read_bytes()))
    if not rows:
        empty += 1
        continue
    first[code], last[code] = rows[0][0], rows[-1][0]
dl = [u for u in U if u["delisted"]]
print(f"[cn] universe {len(U)} (delisted {len(dl)}), files {len(files)}, empty {empty}, "
      f"delisted with data {sum(1 for u in dl if u['code'] in first)}, live without file {sum(1 for u in U if not u['delisted'] and u['code'] not in first)}")
print("[cn] earliest", min(first.values()), "| first-year histogram", sorted(Counter(v[:4] for v in first.values()).items()))
for c in ["600601", "600602", "600651", "600653", "000001", "000002", "000004", "600519", "000651", "601398", "600000"]:
    print(f"[cn] {c}: first {first.get(c)} last {last.get(c)}")
print("[cn] last-date histogram (live)", sorted(Counter(last[u['code']][:7] for u in U if not u['delisted'] and u['code'] in last).items())[-4:])
