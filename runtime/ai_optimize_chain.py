#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai_optimize_chain.py —— 去重 AI 优化全链路（每轮必跑）

输入 : signal_registry.json（跨轮跨维信号基线）
方法 : 复用 dedup2.canonical_name 做「客户级」实体解析，跨轮跨维聚合。
产出 :
  1) rnd/optimize_chain_r<N>.md  —— 客户整合视图（增量信息 / 撞单风险标记）
  2) 控制台摘要（供自动化捕获）

标记定义：
  INCREMENTAL  该客户首现于更早轮次、本轮有新信号 → 存量客户的增量信息
  MULTI_DIM    同一客户横跨 >=2 个维度 → 潜在多头跟进（撞单风险）
  NEW_CUSTOMER 本轮首次出现的客户

用法：python3 ai_optimize_chain.py --round 6
"""
import argparse
import json
import os
import sys
import subprocess
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dedup2 import canonical_name, load_aliases, group_of  # 复用 L2 别名归一 + L4 集团归类

HERE = os.path.dirname(os.path.abspath(__file__))
REG = os.path.join(HERE, "signal_registry.json")
OUT_DIR = os.path.join(HERE, "rnd")


def load_entries():
    d = json.load(open(REG, encoding="utf-8"))
    return d.get("entries", [])


def dedup_status_local(spec: str) -> str:
    """调用 dedup2.check 取单条信号去重状态（NEW/SEEN/FUZZY…）。"""
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "dedup2.py"), "check", spec],
            capture_output=True, text=True, timeout=30)
        out = (r.stdout or "").strip()
        return out.split()[0] if out else "ERR"
    except Exception as e:
        return f"ERR:{e}"


def count_iteration(N: int):
    """统计 R{N} 全部信号相对注册表的去重状态，分离『净新』与『滚动迭代核验』。

    R19 独立审核指出 optimize_chain 长期报『增量=0』系口径失真：迭代任务产生的
    存量高价值信号状态更新被 SEEN/FUZZY 去重拦截、不计入新条目，导致『增量』恒为 0、
    滚动迭代真实产出不可见。本函数把『迭代核验』口径显式化。

    口径（backfill 已落库，故用 canonical_name 存在性判定，避免回查失准）：
      - 存量复用 = 该信号 canonical_name 已存在于注册表 → 即本轮对存量高价值信号的
        重新核验（滚动迭代真义），对应 backfill 的 SEEN+FUZZY。
      - 净新 = canonical_name 不在注册表 → 本轮净新增信号。
    """
    import glob as _glob
    # 优先采用 backfill_registry 落盘的权威去重统计（SEEN/FUZZY 由 dedup2 引擎在写时判定，
    # 比事后 canonical_name 存在性更准——fuzzy 匹配信号 canonical 不同但确为存量复用）。
    bf = os.path.join(HERE, "rnd", f"backfill_r{N}.json")
    if os.path.exists(bf):
        try:
            b = json.load(open(bf, encoding="utf-8"))
            seen = b.get("seen", 0)
            fuzzy = b.get("fuzzy_blocked", 0)
            newc = b.get("added", 0)
            return newc, seen, fuzzy  # (new, seen, fuzzy) —— 调用方算 verified=seen+fuzzy
        except Exception:
            pass
    # 回退：比对 R20 回填前的基线（.bak_r20pre）做 canonical_name 存在性判定。
    baseline_path = os.path.join(HERE, "signal_registry.json.bak_r20pre")
    if os.path.exists(baseline_path):
        base = json.load(open(baseline_path, encoding="utf-8"))
        existing_cn = {e.get("canonical_name", "") for e in base.get("entries", [])}
    else:
        existing_cn = {e.get("canonical_name", "") for e in load_entries()}
    existing_cn.discard("")
    reused = new = 0
    files = _glob.glob(os.path.join(HERE, f"signals_r{N}_*.txt"))
    for f in files:
        for line in open(f, encoding="utf-8"):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            spec = line.split("\t")[0].strip()  # name|geo|type
            if not spec:
                continue
            cn = canonical_name(spec.split("|")[0].strip(), load_aliases())
            if cn in existing_cn:
                reused += 1
            else:
                new += 1
    # 与 backfill 口径对齐：backfill 报 SEEN+FUZZY，此处 reused 即其合计。
    return new, 0, reused


def customer_key(spec, aliases):
    name = spec.split("|")[0].strip()
    return canonical_name(name, aliases)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    args = ap.parse_args()
    N = args.round

    aliases = load_aliases()
    entries = load_entries()

    customers = defaultdict(list)   # customer_key -> [entry...]
    groups_agg = defaultdict(lambda: {"dims": set(), "rounds": set(),
                                      "customers": set(), "n": 0})  # 集团级聚合
    for e in entries:
        name = e["spec"].split("|")[0].strip()
        ck = canonical_name(name, aliases)
        customers[ck].append(e)
        g = group_of(name) or ck  # 无集团前缀则回退到客户自身
        ga = groups_agg[g]
        ga["dims"].add(e["dim"])
        ga["rounds"].add(e["first_seen_round"])
        ga["customers"].add(ck)
        ga["n"] += 1

    inc = []        # 增量信息（客户级）
    newc = []       # 新客户
    total_customers = len(customers)

    # R20 修复（R19 独立审核 action②）：显式化「滚动迭代核验」口径，避免『增量=0』
    # 失真。NEW=净新客户；SEEN+FUZZY=本轮被重新核验的存量高价值信号（迭代真义）。
    iter_new, iter_seen, iter_fuzzy = count_iteration(N)
    iter_verified = iter_seen + iter_fuzzy

    for ck, es in customers.items():
        rounds = sorted({e["first_seen_round"] for e in es})
        dims = sorted({e["dim"] for e in es})
        in_this_round = any(e.get("first_seen_round") == N for e in es)
        first = min(rounds)
        tag = None
        if first == N:
            newc.append((ck, len(es), dims, rounds))
            tag = "NEW"
        else:
            if in_this_round:
                inc.append((ck, len(es), dims, rounds))
                tag = "INCREMENTAL"
        for e in es:
            e["_tag"] = tag

    # 集团级撞单（跨 >=2 维度触达 → 潜在多头跟进）
    multi_group = []
    for g, ga in groups_agg.items():
        if len(ga["dims"]) >= 2:
            multi_group.append((g, ga["n"], sorted(ga["dims"]),
                                sorted(ga["rounds"]), len(ga["customers"])))

    # 输出 markdown
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"optimize_chain_r{N}.md")
    lines = []
    lines.append(f"# 去重 AI 优化全链路 · R{N} 客户整合视图\n")
    lines.append(f"- 客户实体总数：**{total_customers}**（来自 {len(entries)} 条信号，跨 R2–R{N}）")
    lines.append(f"- 增量信息（存量客户本轮新信号，去重口径）：**{len(inc)}**")
    lines.append(f"- 滚动迭代核验（R19 审核 action② 口径修正）：**{iter_verified}** 条存量高价值信号本轮被重新核验（SEEN 再确认 {iter_seen} + FUZZY 去重拦截 {iter_fuzzy}）；其中净新客户 {iter_new} 条")
    lines.append(f"- 集团级撞单风险（同一集团跨 ≥2 维度）：**{len(multi_group)}**")
    lines.append(f"- 本轮新客户：**{len(newc)}**\n")
    lines.append(f"> 口径说明：旧『增量=0』系迭代产生的存量信号状态更新被 SEEN/FUZZY 去重拦截、不计入新条目所致，非迭代停滞。真实滚动迭代产出 = 滚动迭代核验 {iter_verified} 条 + 净新信号 {iter_new} 条。")

    if multi_group:
        lines.append("## 零、集团级撞单风险（同一集团被多维度触达 → 建议定主跟+协作者，避免多头跟进）\n")
        for g, n, dims, rounds, ncus in sorted(multi_group, key=lambda x: -x[1]):
            lines.append(f"- **{g}** ｜ {n} 条/{ncus} 客户 ｜ 维度 {dims} ｜ 轮次 {rounds}")
    if inc:
        lines.append("\n## 一、增量信息（建议优先并入存量客户主跟，勿另起炉灶）\n")
        for ck, n, dims, rounds in sorted(inc, key=lambda x: -x[1]):
            lines.append(f"- **{ck}** ｜ 信号 {n} 条 ｜ 维度 {dims} ｜ 首现 R{min(rounds)}")
    if newc:
        lines.append("\n## 二、本轮新客户（无历史，可自由分配）\n")
        for ck, n, dims, rounds in sorted(newc, key=lambda x: -x[1]):
            lines.append(f"- **{ck}** ｜ {n} 条 ｜ 维度 {dims}")

    lines.append("\n---\n*本视图由 ai_optimize_chain.py 自动生成（dedup2.canonical_name 客户级归一 + group_of 集团级归类）。分配建议需接入「外拓多维表格」真值源后启用（见 rnd/RND_SEED_customer_dedup_assignment.md）。*")
    open(out, "w", encoding="utf-8").write("\n".join(lines))

    print(f"[optimize_chain] customers={total_customers} incremental={len(inc)} "
          f"multi_group={len(multi_group)} new={len(newc)} "
          f"iter_verified={iter_verified}(seen={iter_seen}+fuzzy={iter_fuzzy})")
    print(f"[optimize_chain] wrote {out}")
    return {"customers": total_customers, "incremental": len(inc),
            "multi_group": len(multi_group), "new": len(newc),
            "iter_verified": iter_verified, "out": out}


if __name__ == "__main__":
    main()
