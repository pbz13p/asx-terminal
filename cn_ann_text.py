"""A-share announcement full text, important kinds only (China has no "price sensitive" flag, so we pick by title).

Reads the cninfo index (data/cn_ann/{code}.json.gz, from cn_ann.py), keeps announcements whose titles match the kinds
that move prices (业绩预告/快报, 重大合同/中标, 增减持, 回购, 重组/收购, 定增, 股权激励, 立案/处罚/问询, 风险提示,
停复牌, 诉讼, 退市 ...), drops boilerplate (法律意见书, 独立董事意见, 章程/制度, 英文版, 摘要 of long reports ...),
downloads the PDF from cninfo's static server and keeps the text of the first pages.
Newest first, resumable with a time budget; output data/cn_ann_text/{code}.json.gz = {id: {t, title, kind, pages, text}}.
Env CNTXT_MINUTES (default 170), CNTXT_PAGES (default 6), CNTXT_SINCE (default 2016-01-01), CNTXT_SHARD (k/N, default 0/1).
Quality: a PDF that fails is retried in later runs (up to 3 tries); an empty text is kept as an error, not as data.
"""
import gzip
import io
import json
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

IDX = Path("data/cn_ann")
OUT = Path("data/cn_ann_text")
BASE = "http://static.cninfo.com.cn/"
BUDGET = 60 * float(os.environ.get("CNTXT_MINUTES", "170"))
PAGES = int(os.environ.get("CNTXT_PAGES", "6"))
SINCE = os.environ.get("CNTXT_SINCE", "2016-01-01")
KINDS = [
    ("业绩预告/快报", r"业绩预告|业绩快报|业绩预增|业绩预减|预盈|预亏|扭亏"),
    ("定期报告摘要", r"(年度|半年度|第[一三]季度|季度)报告摘要|季度报告$|一季度报告|三季度报告"),
    ("重大合同/中标", r"重大合同|中标|签订.*(合同|协议)|框架协议|订单"),
    ("增减持", r"增持|减持"),
    ("回购", r"回购"),
    ("重组/收购/出售", r"重大资产|重组|收购|并购|出售资产|购买资产|吸收合并|借壳"),
    ("定增/配股/可转债", r"非公开发行|向特定对象发行|定增|配股|可转换公司债|可转债"),
    ("股权激励/员工持股", r"股权激励|限制性股票|员工持股"),
    ("监管/处罚/问询", r"立案|处罚|警示函|问询函|关注函|监管函|纪律处分"),
    ("风险提示/异常波动", r"风险提示|异常波动|严重异常"),
    ("停复牌", r"停牌|复牌"),
    ("诉讼/仲裁", r"诉讼|仲裁"),
    ("退市/ST", r"退市|终止上市|撤销.*风险警示|实施.*风险警示|\*ST"),
    ("控制权变更", r"控制权|控股股东.*变更|实际控制人.*变更|协议转让"),
    ("分红送转", r"利润分配|分红|送转|转增"),
    ("新产品/技术/批文", r"获得.*(批准|注册|证书|批件)|药品|临床|新产品|专利"),
]
SKIP = r"法律意见|独立董事|章程|制度|规则|英文|English|核查意见|保荐|审计报告|鉴证|会计师|内部控制|社会责任|ESG|监事会|股东大会通知|会议资料|提示性公告的更正|更正|补充公告"


def kind(title):
    if not title or re.search(SKIP, title):
        return None
    for k, pat in KINDS:
        if re.search(pat, title):
            return k
    return None


def pdf_text(url):
    from pypdf import PdfReader
    with urllib.request.urlopen(urllib.request.Request(BASE + url, headers={"Accept": "application/pdf"}), timeout=60) as r:
        body = r.read()
    rd = PdfReader(io.BytesIO(body))
    n = len(rd.pages)
    text = "\n".join((rd.pages[i].extract_text() or "") for i in range(min(PAGES, n)))
    text = text.encode("utf-8", "replace").decode("utf-8")  # some PDFs yield lone surrogates, which crashed the save
    return re.sub(r"[ \t]+", " ", text).strip(), n


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    files = sorted(IDX.glob("*.json.gz"))
    shard = os.environ.get("CNTXT_SHARD", "0/1")          # "k/N": this job handles every N-th stock, so N jobs run side by side
    k, n = (int(x) for x in shard.split("/"))
    files = [f for i, f in enumerate(files) if i % n == k]
    stats = {"stocks": len(files), "selected": 0, "have": 0, "fetched": 0, "failed": 0}
    by_kind = {}
    left = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        for f in files:
            code = f.name[:-8]
            items = json.loads(gzip.decompress(f.read_bytes()))
            sel = []
            for a in items:
                k = kind(a.get("title"))
                if k and a.get("url", "").lower().endswith(".pdf") and a.get("t", "") >= SINCE and (a.get("kb") or 0) <= 6000:
                    sel.append((a, k))
            sel.sort(key=lambda x: x[0]["t"], reverse=True)
            stats["selected"] += len(sel)
            for _, k in sel:
                by_kind[k] = by_kind.get(k, 0) + 1
            p = OUT / f"{code}.json.gz"
            have = json.loads(gzip.decompress(p.read_bytes())) if p.exists() else {}
            todo = [(a, k) for a, k in sel if str(a["id"]) not in have or ("error" in have[str(a["id"])] and have[str(a["id"])].get("tries", 1) < 3)]
            stats["have"] += len(sel) - len(todo)
            if not todo:
                continue
            if time.time() - t0 > BUDGET:
                left += len(todo)
                continue

            def get(x):
                a, k = x
                rec = {"t": a["t"], "title": a["title"], "kind": k}
                try:
                    text, n = pdf_text(a["url"])
                    if len(text) < 30:
                        raise ValueError("empty text (scanned PDF?)")
                    rec.update(pages=n, text=text)
                except Exception as e:
                    prev = have.get(str(a["id"]), {})
                    rec.update(error=type(e).__name__, tries=prev.get("tries", 0) + 1)
                time.sleep(0.2)
                return str(a["id"]), rec
            for i in range(0, len(todo), 40):
                if time.time() - t0 > BUDGET:
                    left += len(todo) - i
                    break
                for ids, rec in ex.map(get, todo[i:i + 40]):
                    have[ids] = rec
                    stats["fetched"] += 1
                    stats["failed"] += "error" in rec
                p.write_bytes(gzip.compress(json.dumps(have, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "replace")))
    Path("data/.cn_ann_text_left").write_text(str(left))
    stats["shard"] = shard
    stats.update(left=left, minutes=round((time.time() - t0) / 60, 1), by_kind=by_kind)
    print("[cn_ann_text]", json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
