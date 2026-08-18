#!/usr/bin/env python3
"""回填信号注册表：补 R4 缺失信号 + 加 R5 新信号。
仅操作内部去重基线 signal_registry.json，不触发任何飞书/Bitable 写入。
dedup2.add 内置 L1-L4 去重：SEEN 跳过、FUZZY 拦截（需 --force）。
"""
import os, glob, importlib.util, json

HERE = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, HERE)
import dedup2 as D

DIMS = ["mice", "one", "two", "three", "four", "five", "six",
        "potentialsource", "broardsignal", "tmc"]

def parse_line(line):
    line = line.rstrip("\n")
    if not line.strip():
        return None
    if line.lstrip().startswith("#"):  # 跳过 txt 注释/表头行，避免污染注册表(R23 发现)
        return None
    parts = line.split("\t")
    spec = parts[0]
    rice = 0
    for p in parts[1:]:
        try:
            rice = int(float(p))
            break
        except ValueError:
            pass
    desc = parts[-1] if len(parts) > 1 else ""
    return spec, rice, desc

def run(round_n):
    added = skipped = fuzzy = 0
    print(f"\n===== R{round_n} 回填 =====")
    for dim in DIMS:
        fn = os.path.join(HERE, f"signals_r{round_n}_{dim}.txt")
        if not os.path.exists(fn):
            continue
        for line in open(fn, encoding="utf-8"):
            r = parse_line(line)
            if not r:
                continue
            spec, rice, desc = r
            before = len(D.load()["entries"])
            # 捕获 add 的判定
            status, hit = D.check(spec, verbose=False)
            if status == "SEEN":
                skipped += 1
                continue
            if status == "FUZZY":
                fuzzy += 1
                print(f"  FUZZY-BLOCKED [{dim}] {spec} ~ {hit['spec']}")
                continue
            D.add(spec, dim, round_n, note=desc[:60], rice=rice)
            added += 1
    print(f"  R{round_n}: added={added} skipped(SEEN)={skipped} fuzzy_blocked={fuzzy}")
    # 落盘权威去重统计，供 optimize_chain 读取「滚动迭代核验」口径（R19 审核 action②）
    try:
        import os as _os
        _od = _os.path.join(HERE, "rnd")
        _os.makedirs(_od, exist_ok=True)
        _d = D.load()
        json.dump({"round": round_n, "added": added, "seen": skipped,
                   "fuzzy_blocked": fuzzy,
                   "total_before": len(_d["entries"]) - added,
                   "total_after": len(_d["entries"]),
                   "updated_round": _d.get("updated_round")},
                  open(_os.path.join(_od, f"backfill_r{round_n}.json"), "w"),
                  ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [warn] backfill stat 写盘失败: {e}")
    return added, skipped, fuzzy

if __name__ == "__main__":
    a4 = run(4)
    a5 = run(5)
    d = D.load()
    print(f"\n=== 回填后注册表: {len(d['entries'])} 条, updated_round={d['updated_round']} ===")
