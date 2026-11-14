"""China A-share announcement index from cninfo (巨潮资讯, the CSRC-designated disclosure site), personal research use.
Step 1 (this script): the list of every announcement per main-board stock since 2016 -- date, title, type, PDF link.
Titles alone carry most of the signal in China (业绩预告, 增持/减持, 回购, 中标, 立案调查 ...). PDFs come later, selectively.
Polite: one request at a time, small pause, resumable with a time budget; output data/cn_ann/{code}.json.gz.
"""
import gzip
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path("data/cn_ann")
CN = Path("data/cn")
Q = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
STOCKS = "http://www.cninfo.com.cn/new/data/szse_stock.json"
START = os.environ.get("CNANN_START", "2016-01-01")
BUDGET = 60 * float(os.environ.get("CNANN_MINUTES", "170"))
MAIN = ("60", "000", "001", "002", "003")
stats = {"requests": 0, "failed": 0}


def req(url, data=None, tries=4):
    body = urllib.parse.urlencode(data).encode() if data else None
    for k in range(tries):
        try:
            r = urllib.request.Request(url, data=body, headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})
            with urllib.request.urlopen(r, timeout=40) as f:
                stats["requests"] += 1
                return json.loads(f.read())
        except Exception as e:
            if k == tries - 1:
                stats["failed"] += 1
                raise
            time.sleep(3 + 5 * k)


def org_ids():
    try:
        d = req(STOCKS)
        return {s["code"]: s["orgId"] for s in d.get("stockList", [])}
    except Exception as e:
        print("[orgId list failed]", type(e).__name__, e)
        return {}


def one(code, org, today):
    sh = code.startswith("6")
    items, page = [], 1
    while True:
        d = req(Q, {"pageNum": page, "pageSize": 30, "column": "sse" if sh else "szse", "tabName": "fulltext", "plate": "",
                    "stock": f"{code},{org}", "searchkey": "", "secid": "", "category": "", "trade": "",
                    "seDate": f"{START}~{today}", "sortName": "", "sortType": "", "isHLtitle": "true"})
        for a in d.get("announcements") or []:
            items.append({"t": time.strftime("%Y-%m-%d", time.gmtime(a["announcementTime"] / 1000 + 8 * 3600)),
                          "title": a.get("announcementTitle"), "id": a.get("announcementId"), "type": a.get("announcementType"),
                          "url": a.get("adjunctUrl"), "kb": a.get("adjunctSize")})
        if not d.get("hasMore") or page > 400:
            break
        page += 1
        time.sleep(0.3)
    return items


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    uni = json.loads((CN / "universe.json").read_text()) if (CN / "universe.json").exists() else []
    codes = sorted({u["code"] for u in uni if u["code"].startswith(MAIN)})
    orgs = org_ids()
    print("[start]", len(codes), "codes,", len(orgs), "orgIds")
    done = n_new = 0
    errors = []
    for code in codes:
        f = OUT / f"{code}.json.gz"
        if f.exists():
            done += 1
            continue
        if time.time() - t0 > BUDGET:
            break
        org = orgs.get(code) or (("gssh0" if code.startswith("6") else "gssz0") + code)
        try:
            items = one(code, org, today)
            f.write_bytes(gzip.compress(json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode()))
            n_new += 1
            done += 1
            if n_new <= 3 or n_new % 200 == 0:
                print("[ok]", code, len(items), "items", f"{time.time() - t0:.0f}s", stats)
        except Exception as e:
            errors.append(f"{code} {type(e).__name__}")
            if len(errors) >= 30 and n_new == 0:
                print("[abort] nothing works:", errors[:5])
                break
        time.sleep(0.3)
    left = len(codes) - done
    total_items = 0
    for g in OUT.glob("*.json.gz"):
        try:
            total_items += len(json.loads(gzip.decompress(g.read_bytes())))
        except Exception:
            pass
    summary = {"codes": len(codes), "done": done, "new_this_run": n_new, "left": left, "announcements": total_items,
               "errors": len(errors), "error_sample": errors[:20], **stats, "minutes": round((time.time() - t0) / 60, 1)}
    Path("data/.cn_ann_left").write_text(str(left))
    print(json.dumps(summary, ensure_ascii=False))
    out = os.environ.get("CNANN_OUT")
    if out:
        Path(out).write_text(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
