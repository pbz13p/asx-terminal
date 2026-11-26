"""Write docs/brief.md: a short status snapshot (<= 8 KB) that every scheduled task reads first (Frank 2026-11-26).

Pure code, no API. Works in pbz13p/asx-terminal and pbz13p/howzu (same file in both; it detects which repo it is in).
Every line it copies keeps the exact words of the source (cells only cut at 120 characters, marked "…"), and every
section says which file and line numbers it came from, so a task reads only the part it needs.
Self-check: each copied piece is searched for, word for word, in its source line; a mismatch stops the script.
"""
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "brief.md"
LIMIT = 8192
CUT = 120
CHECKS = []          # (piece, source line) pairs for the self-check


def read(rel):
    p = ROOT / rel
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def cut(s, n=CUT):
    s = s.strip()
    return s if len(s) <= n else s[:n] + "…"


def keep(piece, src):
    """Remember a copied piece (minus the trailing …) to check it against its source line."""
    CHECKS.append((piece[:-1] if piece.endswith("…") else piece, src))
    return piece


def section(lines, start_pat):
    """(start, end) line indexes of the '## ...' section whose heading matches start_pat."""
    s = next((i for i, l in enumerate(lines) if re.match(start_pat, l)), None)
    if s is None:
        return None, None
    lvl = len(re.match(r"#+", lines[s]).group(0))
    e = next((j for j in range(s + 1, len(lines)) if re.match(r"#{1,%d} " % lvl, lines[j])), len(lines))
    return s, e


def cells(row):
    return [c.strip() for c in row.strip().strip("|").split("|")]


def sort_table(lines, rel):
    s, e = section(lines, r"##\s*排序表")
    if s is None:
        return []
    out = [f"## 排序表（原文 {rel} 第 {s + 1}–{e} 行）"]
    hdr = None
    for i in range(s + 1, e):
        l = lines[i]
        if not l.startswith("|") or re.match(r"\|\s*-", l):
            continue
        c = cells(l)
        if hdr is None:
            hdr = c
            continue
        num, item, status = c[0], c[1] if len(c) > 1 else "", c[-1]
        out.append(f"- L{i + 1} #{keep(cut(num, 10), l)} {keep(cut(item), l)} ｜状态：{keep(cut(status), l)}")
    return out


def frank_todo(lines, rel):
    s, e = section(lines, r"##\s*待 ?Frank ?处理")
    if s is None:
        return []
    out = [f"## 待 Frank 处理（原文 {rel} 第 {s + 1}–{e} 行；✅ = 已完成）"]
    for i in range(s + 1, e):
        l = lines[i]
        if re.match(r"(###\s+|-\s+(\S*F\d+|~~|✅))", l):
            t = re.sub(r"^(###\s+|-\s+)", "", l)
            done = "✅" in t or t.startswith("~~")
            out.append(f"- L{i + 1} {'✅' if done else '⬜'} {keep(cut(t, 90), l)}")
    return out


VERDICT = re.compile(r"全部无效|无效|有效|一般|线索")


CONCL = re.compile(r"^\s*[-*]?\s*(\*\*)?\s*(v\d+\s*)?(结果|结论)\s*[（(：:]")


def rounds(lines, rel):
    """One line per research round: its ROADMAP heading + the line that starts with 结果 / 结论 (bold or not)."""
    out = [f"## 研究各轮（原文 {rel}，L = 该轮标题行；结论行另注行号）"]
    best = {}
    for i, h in enumerate(lines):
        m = re.match(r"#{2,3} 第 ?(\d+)", h)
        if not m:
            continue
        n = int(m.group(1))
        end = next((j for j in range(i + 1, len(lines)) if re.match(r"#{1,2} ", lines[j])), len(lines))
        cands = [j for j in range(i + 1, end) if CONCL.match(lines[j])]
        concl = next((j for j in cands if "结论" in lines[j][:16]), cands[-1] if cands else None)
        if n in best and (best[n][2] is not None or concl is None):
            continue
        fn = re.search(r"study\d+\.py", "\n".join(lines[i:end]))
        fn = f"study{n}.py" if (ROOT / f"study{n}.py").exists() else (fn.group(0) if fn else "—")
        best[n] = (i, fn, concl)
    for n in sorted(best):
        i, fn, concl = best[n]
        h = lines[i]
        if concl is None:
            out.append(f"- 第{n}轮 L{i + 1} {fn} ｜{keep(cut(re.sub(r'^#+\s*', '', h), 70), h)} ｜结论：没有结论行（见原文）")
            continue
        c = lines[concl]
        v = VERDICT.search(c)
        out.append(f"- 第{n}轮 L{i + 1} {fn} ｜结论 L{concl + 1}：{keep(cut(re.sub(r'^[-*\s]+', '', c), 110), c)}"
                   f" ｜{v.group(0) if v else '见原文'}")
    return out


def recent_entries(lines, rel, n, title, pat=r"#{2,3} "):
    heads = [i for i, l in enumerate(lines) if re.match(pat, l)]
    out = [f"## {title}（原文 {rel}）"]
    for i in heads[-n:]:
        first = next((lines[j] for j in range(i + 1, min(i + 8, len(lines))) if lines[j].strip()), "")
        out.append(f"- L{i + 1} {keep(cut(lines[i].lstrip('# '), 70), lines[i])} ｜{keep(cut(first.lstrip('- '), 110), first) if first else ''}")
    return out


DATE = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")


def quant_week(lines, rel):
    heads = [(i, DATE.search(l)) for i, l in enumerate(lines) if re.match(r"#{2,3} ", l) and DATE.search(l)]
    if not heads:
        return []
    newest = max(date(*map(int, m.groups())) for _, m in heads)
    out = [f"## 量化方法追踪最新一周（原文 {rel}）"]
    for i, m in heads:
        if date(*map(int, m.groups())) >= newest - timedelta(days=6):
            out.append(f"- L{i + 1} {keep(cut(lines[i].lstrip('# '), 100), lines[i])}")
    return out


TOTAL = re.compile(r"(?:总计|合计)\s*约?\s*([\d,]+)")


def usage(files, git=True, days=7):
    """Last 7 days of 「用量：」 lines: task name + total only."""
    today = date.today()
    rows = []
    for rel, default in files:
        lines = read(rel)
        task, d = default, None
        for i, l in enumerate(lines):
            if re.match(r"#{2,3} ", l):
                m = DATE.search(l)
                d = date(*map(int, m.groups())) if m else d
                par = re.search(r"[（(]([^）)]+)[）)]", l)
                task = par.group(1) if par and not DATE.fullmatch(par.group(1)) else default
            if "用量：" in l and d and d >= today - timedelta(days=days):
                t = TOTAL.search(l)
                rows.append((d, task, t.group(1) if t else "未能获取", f"{rel} L{i + 1}"))
    if git:
        try:
            log = subprocess.run(["git", "log", f"--since={days} days ago", "--format=%x1e%ad%x1f%s%x1f%b", "--date=short"],
                                 cwd=ROOT, capture_output=True, text=True, timeout=60).stdout
        except Exception:
            log = ""
        for rec in log.split("\x1e"):
            if "用量：" not in rec:
                continue
            dd, subj, body = (rec.split("\x1f") + ["", ""])[:3]
            line = next((x for x in body.splitlines() if "用量：" in x), "")
            t = TOTAL.search(line)
            name = "今日要闻" if "要闻" in subj else ("经济热点快讯" if "新闻" in subj or "news" in subj.lower() else cut(subj, 24))
            rows.append((date.fromisoformat(dd.strip()), name, t.group(1) if t else "未能获取", "git log"))
    out = [f"## 近 7 天用量（任务名 · token 合计；来源见每行）"]
    for d, task, tot, src in sorted(rows, reverse=True):
        out.append(f"- {d.isoformat()} {task}：{tot}（{src}）")
    if len(out) == 1:
        out.append("- 近 7 天没有找到「用量：」行")
    return out


def howzu_metrics(lines, rel):
    out = [f"## 三个考核数（原文 {rel}）"]
    i = next((k for k, l in enumerate(lines) if "**月访问量**" in l), None)
    if i is not None:
        out.append(f"- L{i + 1} {keep(cut(lines[i].lstrip('- '), 200), lines[i])}")
    t = [k for k, l in enumerate(lines) if l.startswith("| 20")]
    wk = [k for k in t if k < (next((j for j, l in enumerate(lines) if l.startswith("## ⏰")), len(lines)))]
    if wk:
        k = wk[-1]
        c = cells(lines[k])
        out.append(f"- L{k + 1} 周表最新一行：{keep(cut(' | '.join(c[:4]), 200), lines[k])}")
    return out


def build():
    asx = (ROOT / "study.py").exists()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    head = [f"# 状态快照（tools/make_brief.py 自动生成，{stamp}；别手改）",
            "用法：先读这里；要做哪一项、要核实哪个数字，再按行号读原文那一段；判断不清就读全，不要猜。", ""]
    rm = read("ROADMAP.md")
    parts = [sort_table(rm, "ROADMAP.md"), frank_todo(rm, "ROADMAP.md")]
    if asx:
        parts += [rounds(rm, "ROADMAP.md"), quant_week(read("research/quant_watch.md"), "research/quant_watch.md"),
                  recent_entries(read("docs/progress.md"), "docs/progress.md", 3, "进度最近 3 条", r"## "),
                  usage([("docs/progress.md", "股票研究员"), ("research/quant_watch.md", "量化方法每周追踪")])]
    else:
        parts += [howzu_metrics(read("METRICS.md"), "METRICS.md"),
                  recent_entries(read(".auto-improve/LOG.md"), ".auto-improve/LOG.md", 3, "改进日志最近 3 条"),
                  usage([(".auto-improve/LOG.md", "好租产品增长组"), (".auto-improve/DEMAND.md", "租客需求周报")])]
    return head, parts


def render(head, parts):
    return "\n".join(head + [l for p in parts for l in (p + [""])]) + "\n"


def shrink(head, parts):
    """Over 8 KB: cut the oldest parts first (oldest rounds, then done rows, then oldest usage), never rewrite words."""
    txt = render(head, parts)
    names = [p[0] if p else "" for p in parts]
    ri = next((k for k, n in enumerate(names) if n.startswith("## 研究各轮")), None)
    si = next((k for k, n in enumerate(names) if n.startswith("## 排序表")), None)
    ui = next((k for k, n in enumerate(names) if n.startswith("## 近 7 天用量")), None)
    while len(txt.encode("utf-8")) > LIMIT:
        if ri is not None and len(parts[ri]) > 8:
            parts[ri].pop(1)                     # oldest round first
            if not parts[ri][-1].startswith("- （更早"):
                parts[ri].append("- （更早的轮次见 ROADMAP 各「第 N 轮」一节）")
        elif si is not None and any("｜状态：✅" in l for l in parts[si][1:]):
            k = next(k for k, l in enumerate(parts[si]) if "｜状态：✅" in l)
            parts[si].pop(k)                     # finished rows go before open ones
            if not parts[si][-1].startswith("- （已完成"):
                parts[si].append("- （已完成的 ✅ 行省略，见原文）")
        elif ui is not None and len(parts[ui]) > 4:
            parts[ui].pop(-1)                    # oldest usage line
        else:
            for p in parts:                      # last resort: shorter cells
                for k in range(1, len(p)):
                    if len(p[k]) > 90:
                        p[k] = p[k][:90] + "…"
            txt = render(head, parts)
            break
        txt = render(head, parts)
    return txt


def self_check():
    bad = [(p, s) for p, s in CHECKS if p and p not in s]
    if bad:
        for p, s in bad[:5]:
            print("MISMATCH:", p[:80], "|", s[:80], file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    h, ps = build()
    self_check()
    text = shrink(h, ps)
    OUT.parent.mkdir(exist_ok=True)
    old = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    if re.sub(r"\d{4}-\d\d-\d\d \d\d:\d\d", "", old) == re.sub(r"\d{4}-\d\d-\d\d \d\d:\d\d", "", text):
        print("brief unchanged")
    else:
        OUT.write_text(text, encoding="utf-8")
        print(f"brief written: {len(text.encode('utf-8'))} bytes")
