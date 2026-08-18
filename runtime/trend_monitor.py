#!/usr/bin/env python3
"""
trend_monitor.py · 市场联邦元监控层（R17 后新增）

解决的问题：自动化管线是开环的——每轮只写摘要，从不比对自身历史，
导致「内容缩水 / 增量恒 0 / 坏轮（R14=1、R15 缺失）/ harvest 失效」长期无人察觉。

功能：
  1. 聚合各轮 signals_r*.txt 计数，计算 R<N> vs R<N-1> 总量/逐维 delta。
  2. 检测 增量=0 连续 ≥ N 轮。
  3. 检测近空轮（单轮 < MIN_SIGNALS）。
  4. 检测 harvest 源新鲜度（> STALE_DAYS 天即告警）。
  5. 任一告警 → 打印 STAGNATION_ALERT 并退出码 1（供自动化分支 / 飞书推送）。

用法：
  python3 trend_monitor.py                  # 跑全量检测 + 写 JSON
  python3 trend_monitor.py --json out.json  # 指定输出
"""
import os, re, sys, json, glob, datetime, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_SIGNALS = 5          # 单轮低于此值视为近空轮
STALE_DAYS = 7           # harvest 源龄超过此天数视为内层失效
INC_ZERO_ALERT = 3       # 增量连续 0 达到此轮数告警

def signal_counts():
    pat = re.compile(r"^signals_r(\d+)_(.+)\.txt$")
    rows = {}
    for f in glob.glob(os.path.join(HERE, "signals_r*.txt")):
        m = pat.match(os.path.basename(f))
        if not m:
            continue
        r, dim = int(m.group(1)), m.group(2)
        with open(f, encoding="utf-8") as fh:
            n = sum(1 for l in fh.read().splitlines() if l.strip() and not l.strip().startswith("#"))
        rows.setdefault(r, {})[dim] = n
    return rows

def incremental_counts():
    out = {}
    for f in glob.glob(os.path.join(HERE, "rnd", "optimize_chain_r*.md")):
        m = re.search(r"optimize_chain_r(\d+)\.md$", f)
        if not m:
            continue
        r = int(m.group(1))
        txt = open(f, encoding="utf-8").read()
        mm = re.search(r"增量[^\d]*(\d+)", txt)
        out[r] = int(mm.group(1)) if mm else None
    return out

def harvest_freshness():
    ages = {}
    for f in glob.glob(os.path.join(HERE, "harvest", "*.json")):
        age = (datetime.datetime.now() - datetime.datetime.fromtimestamp(os.path.getmtime(f))).days
        ages[os.path.basename(f)] = age
    return ages

def main():
    rows = signal_counts()
    inc = incremental_counts()
    rounds = sorted(rows)
    alerts = []
    report = {"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
              "rounds": [], "alerts": []}

    prev_total = None
    for r in rounds:
        d = rows[r]
        total = sum(d.values())
        delta = (total - prev_total) if prev_total is not None else None
        pct = (delta / prev_total * 100) if prev_total else None
        inc_v = inc.get(r)
        entry = {"round": r, "total": total, "dims": len(d),
                 "delta_vs_prev": delta, "delta_pct": round(pct, 1) if pct is not None else None,
                 "incremental": inc_v}
        # 近空轮
        if total < MIN_SIGNALS:
            msg = f"R{r}: 近空轮（仅 {total} 条信号，<{MIN_SIGNALS}），疑似扫描失败"
            alerts.append(msg); entry["flag_near_empty"] = True
        # 增量 0
        if inc_v == 0:
            entry["flag_inc_zero"] = True
        report["rounds"].append(entry)
        prev_total = total

    # 增量连续 0 检测
    zero_streak = 0
    streak_rounds = []
    for r in sorted(inc):
        v = inc[r]
        if v == 0:
            zero_streak += 1; streak_rounds.append(r)
        else:
            if zero_streak >= INC_ZERO_ALERT:
                alerts.append(f"增量连续 0：R{streak_rounds[0]}–R{streak_rounds[-1]} 共 {zero_streak} 轮（迭代停滞）")
            zero_streak = 0; streak_rounds = []
    if zero_streak >= INC_ZERO_ALERT:
        alerts.append(f"增量连续 0：R{streak_rounds[0]}–R{streak_rounds[-1]} 共 {zero_streak} 轮（迭代停滞）")

    # harvest 新鲜度
    ages = harvest_freshness()
    if ages:
        max_age = max(ages.values())
        stale = [k for k, v in ages.items() if v >= STALE_DAYS]
        if stale:
            alerts.append(f"内层 harvest 源龄最大 {max_age} 天（≥{STALE_DAYS}），共 {len(stale)} 个专群 JSON 失效，双层退化单层")
            report["harvest_max_age_days"] = max_age
            report["harvest_stale_count"] = len(stale)

    report["alerts"] = alerts
    out_json = os.path.join(HERE, "rnd", f"trend_monitor_{datetime.datetime.now():%Y%m%d}.json")
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    # 打印
    print("=== 市场联邦趋势监控 ===")
    print(f"{'轮':>4} {'总量':>4} {'维':>3} {'Δvs前':>7} {'Δ%':>7} {'增量':>4}  标记")
    for e in report["rounds"]:
        flags = []
        if e.get("flag_near_empty"): flags.append("近空")
        if e.get("flag_inc_zero"): flags.append("增量0")
        dp = f"{e['delta_vs_prev']:+d}" if e["delta_vs_prev"] is not None else "  - "
        pp = f"{e['delta_pct']:+.0f}%" if e["delta_pct"] is not None else "  - "
        print(f"R{e['round']:>3} {e['total']:>4} {e['dims']:>3} {dp:>7} {pp:>7} {str(e['incremental']):>4}  {' '.join(flags)}")
    print()
    if alerts:
        print("⚠️ STAGNATION_ALERT（共 %d 项）:" % len(alerts))
        for a in alerts:
            print("  - " + a)
        print(f"\n详情已写入 {out_json}")
        return 1
    else:
        print("✅ 无停滞/异常告警")
        print(f"\n详情已写入 {out_json}")
        return 0

if __name__ == "__main__":
    sys.exit(main())
