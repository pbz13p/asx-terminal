"""A-share announcements -> vectors with a free local Chinese model (Frank 2026-11-17: free first, then paid within the credit).

Same idea as round 81 for the ASX: every announcement in the A-share text caches (data/cn_ann_text, plus the big-PDF and
OCR caches) becomes one vector = mean of its chunk vectors (BAAI/bge-small-zh-v1.5 on the Actions CPU, no API cost).
Long documents: up to MAX_CHUNKS chunks of ~CHUNK characters spread evenly over the whole text, so every part counts.
Output: data/cn_emb/<code>.npz (ids, vectors float16). One lane per text-cache shard; resumable; a time budget per run.
"""
import gzip
import json
import os
import time
from pathlib import Path

import numpy as np

SRC = Path("data/cn_ann_text")
EXTRA = [Path("data/cn_ann_big"), Path("data/cn_ann_ocr")]
OUT = Path("data/cn_emb")
MODEL = "BAAI/bge-small-zh-v1.5"
CHUNK = 500          # characters (Chinese) per chunk
MAX_CHUNKS = 60
BUDGET = 60 * float(os.environ.get("EMB_MINUTES", "300"))


def chunks(text):
    t = " ".join(text.split())
    if not t:
        return []
    parts = [t[i:i + CHUNK] for i in range(0, len(t), CHUNK)]
    if len(parts) > MAX_CHUNKS:   # spread over the whole document, not just the start
        step = len(parts) / MAX_CHUNKS
        parts = [parts[int(k * step)] for k in range(MAX_CHUNKS)]
    return parts


def load(code):
    docs = {}
    for src in [SRC] + EXTRA:
        p = src / f"{code}.json.gz"
        if p.exists():
            for i, r in json.loads(gzip.decompress(p.read_bytes())).items():
                if r.get("text"):
                    docs[i] = (r.get("title") or "") + "。" + r["text"]
    return docs


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    codes = sorted(p.name[:-8] for p in SRC.glob("*.json.gz"))
    mine = {c + ".npz" for c in codes}
    for f in OUT.glob("*.npz"):   # this lane's cache keeps only its own stocks
        if f.name not in mine:
            f.unlink()
    model = None
    done = left = total = 0
    for code in codes:
        docs = load(code)
        total += len(docs)
        f = OUT / f"{code}.npz"
        have_ids, have_vec = [], []
        if f.exists():
            z = np.load(f)
            have_ids, have_vec = list(z["ids"]), list(z["vec"])
        seen = set(have_ids)
        todo = [i for i in docs if i not in seen]
        if not todo:
            continue
        if time.time() - t0 > BUDGET:
            left += len(todo)
            continue
        if model is None:
            from fastembed import TextEmbedding
            model = TextEmbedding(MODEL, threads=os.cpu_count())
        for k in range(0, len(todo), 200):   # save every 200 docs so a timeout loses little
            if time.time() - t0 > BUDGET:
                left += len(todo) - k
                break
            part = todo[k:k + 200]
            texts, owner = [], []
            for j, i in enumerate(part):
                for c in chunks(docs[i]):
                    texts.append(c)
                    owner.append(j)
            if not texts:
                continue
            vecs = np.array(list(model.embed(texts, batch_size=64)), dtype=np.float32)
            owner = np.array(owner)
            for j, i in enumerate(part):
                sel = vecs[owner == j]
                if len(sel):
                    v = sel.mean(0)
                    have_ids.append(i)
                    have_vec.append((v / (np.linalg.norm(v) + 1e-9)).astype(np.float16))
                    done += 1
            np.savez_compressed(f, ids=np.array(have_ids, dtype="U32"), vec=np.array(have_vec, dtype=np.float16))
    Path("cnemb_left.txt").write_text(str(left))
    print("[cn_emb]", json.dumps({"stocks": len(codes), "docs": total, "embedded_now": done, "left": left,
                                  "minutes": round((time.time() - t0) / 60, 1)}))


if __name__ == "__main__":
    main()
