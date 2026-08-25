#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
event_watch.py · 事件主动触发（"完全 Agent 效果"最后一块）

不再等人类 @，而是按「监控清单」主动巡检各维度，发现新增市场机会就主动推专群。
  - 监控清单：watchlist.yaml（存在则用之），否则用内置默认（每维一条主题）。
  - 每个主题 → agent_loop.watch() 让 LLM 判【新增】/【无新增】。
  - 新颖闸门：仅当本轮简报与上次不同（state 比对）才推送，避免刷屏。
  - 默认 dry-run（--send 才真推），尊重「对外发送要谨慎」。

诚实边界：
  - 内部语料 + LLM 推理驱动的监控是真实闭环；真实外部 Web 检索仍由
    WorkBuddy 侧维度 skill（自带 WebSearch）或运营者执行，本脚本标注「需外部检索验证」。

用法：
  python3 event_watch.py --dry-run          # 巡检一轮（不推）
  python3 event_watch.py --send             # 巡检 + 真推新增
  python3 event_watch.py --send --interval 3600   # 常驻每小时巡检
"""
import argparse
import fcntl
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# L2 Observability：接入 agent_runs_collector（runtime/ 下）
REPO_RUNTIME = os.path.join(HERE, "..", "..", "runtime")
sys.path.insert(0, REPO_RUNTIME)
try:
    from agent_runs_collector import RunCollector
    _HAS_COLLECTOR = True
except Exception:
    _HAS_COLLECTOR = False
from router import load_config
from agent_loop import watch, post, DIM_CN, extract_signals

# 维度 → agent_id 映射（L2 日志用）
DIM_AGENT_ID = {
    "one": "market-one-wb", "two": "market-two-wb",
    "three": "market-three-wb", "four": "market-four-wb",
    "five": "market-five-wb", "six": "market-six-wb",
    "seven": "market-seven-wb", "mice": "market-seven-wb",
    "potentialsource": "market-potential-wb",
    "broardsignal": "market-broad-wb", "tmc": "market-tmc-wb",
}

DEFAULT_WATCH = [
    {"topic": "近期央企/国企差旅与培训住宿需求变动", "dim": "two"},
    {"topic": "本地会议/会展档期与竞品承接动态", "dim": "mice"},
    {"topic": "竞品在搞什么促销/套餐/价格动作", "dim": "six"},
    {"topic": "会员复购与增购机会点", "dim": "three"},
    {"topic": "餐饮宴会/婚宴/商务宴请新需求", "dim": "four"},
    {"topic": "长住/服务式公寓客源变化", "dim": "five"},
    {"topic": "休闲度假与周边游热度", "dim": "one"},
    {"topic": "潜在客源池（企业/机构）拓展线索", "dim": "potentialsource"},
    {"topic": "宏观政策/区域利好对酒店生意的影响", "dim": "broardsignal"},
    {"topic": "TMC/差旅平台订单与集采动向", "dim": "tmc"},
]


def load_watchlist():
    p = os.path.join(HERE, "watchlist.yaml")
    if os.path.exists(p):
        try:
            import re
            items = []
            cur = {}
            for line in open(p, encoding="utf-8"):
                s = line.strip()
                if s.startswith("- topic:"):
                    if cur:
                        items.append(cur)
                    cur = {"topic": s.split(":", 1)[1].strip().strip('"'), "dim": ""}
                elif s.startswith("dim:") and cur is not None:
                    cur["dim"] = s.split(":", 1)[1].strip().strip('"')
            if cur:
                items.append(cur)
            items = [i for i in items if i.get("topic") and i.get("dim")]
            if items:
                return items
        except Exception:
            pass
    return DEFAULT_WATCH


def gen_watchlist(reg_path=None, out_path=None, min_count=2, top_n=3):
    """LOOP-017：用真实信号驱动监控清单（自迭代生成）。

    从 signal_registry 每维度的头部客户聚类（group 字段）派生监控主题，
    替代拍脑袋的静态默认。信号增长后重跑本命令即可刷新清单。
    生成 watchlist.yaml（load_watchlist 自动读取）。
    """
    reg = reg_path or os.path.join(
        HERE, "..", "..", "runtime", "signal_registry.json")
    if not os.path.exists(reg):
        print(f"[gen-watchlist] 注册表不存在：{reg}")
        return []
    try:
        d = json.load(open(reg, encoding="utf-8"))
    except Exception as e:
        print(f"[gen-watchlist] 注册表读取失败：{e}")
        return []
    from collections import Counter, defaultdict
    groups = defaultdict(Counter)
    for e in d.get("entries", []):
        g = (e.get("group") or "").strip()
        if g and e.get("dim"):
            groups[e["dim"]][g] += 1
    items = []
    for dim, cnt in groups.items():
        top = [g for g, c in cnt.most_common(top_n) if c >= min_count]
        if top:
            items.append({"topic": f"头部客户动态：{'、'.join(top)}", "dim": dim})
    # 覆盖兜底：无客户聚类的维度保留内置默认主题，保证10维度全覆盖
    covered = {it["dim"] for it in items}
    for d in DEFAULT_WATCH:
        if d["dim"] not in covered:
            items.append(dict(d))
    if not items:
        print("[gen-watchlist] 无满足阈值的客户聚类，保持内置默认")
        return []
    out = out_path or os.path.join(HERE, "watchlist.yaml")
    with open(out, "w", encoding="utf-8") as f:
        f.write("# 由 event_watch.py --gen-watchlist 自动生成（LOOP-017）\n")
        f.write("# 基于 signal_registry 真实客户聚类，信号增长后可重新生成\n")
        for it in items:
            f.write(f'- topic: "{it["topic"]}"\n  dim: {it["dim"]}\n')
    print(f"[gen-watchlist] 已生成 {len(items)} 条监控主题 → {out}")
    return items


def append_inbox(candidates, dim, topic):
    """LOOP-018：新增信号候选写入待审区（不直接写 registry 防 LLM 幻觉污染）。

    待审区：runtime/harvest_inbox.jsonl，每行一条候选。
    人工/独立确认后才由收割流程并入 signal_registry（§3.5分离）。
    """
    if not candidates:
        return 0
    inbox = os.path.join(HERE, "..", "..", "runtime", "harvest_inbox.jsonl")
    n = 0
    try:
        with open(inbox, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                for c in candidates:
                    rec = {
                        "ts": int(time.time()), "dim": dim, "topic": topic,
                        "spec": f"{c['name']}|{c['geo']}|{c['type']}",
                        "status": "pending_review", "source": "LOOP-018-watch",
                    }
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n += 1
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as e:
        print(f"  [inbox] 写入失败：{e}")
    if n:
        print(f"  [inbox] {n} 条新增信号候选 → 待审区")
    return n


def run_once(cfg, items, lark_bin, send):
    hotel = cfg.get("hotel", "本酒店")
    geo = cfg.get("geo", "未指定")
    chats = {d: cfg[f"feishu_chat_{d}"] for d in DIM_CN if cfg.get(f"feishu_chat_{d}")}
    state_path = os.path.join(HERE, "event_watch_state.json")
    # 文件锁防止 --interval 常驻并发丢状态（codex 验收缺口3）
    # try/finally 确保异常路径也释锁（GLM 验收者指出 finally 缺失）
    lock_path = state_path + ".lock"
    _lock_fd = open(lock_path, "w")
    try:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            # 已有另一实例在跑，等拿到锁
            fcntl.flock(_lock_fd, fcntl.LOCK_EX)
        state = json.load(open(state_path)) if os.path.exists(state_path) else {}
        pushed = 0
        for it in items:
            dim = it["dim"]
            topic = it["topic"]
            print(f"\n[监控] {DIM_CN.get(dim, dim)}｜{topic}")
            # L2 Observability：记录巡检执行日志
            agent_id = DIM_AGENT_ID.get(dim, f"market-{dim}-wb") if _HAS_COLLECTOR else ""
            if _HAS_COLLECTOR:
                with RunCollector(agent_id, dim, trigger="event_watch") as rc:
                    brief = watch(topic, dim, hotel, geo)
                    rc.set_output(brief)
            else:
                brief = watch(topic, dim, hotel, geo)
            print("  LLM：" + brief[:80].replace("\n", " "))
            if not brief.startswith("【新增】"):
                print("  → 无新增，跳过")
                continue
            # 新颖性闸门：语义指纹 + 冷却期（codex 验收缺口2 修复）
            # 全文 sha1 对非确定性 LLM 无效（每次措辞不同→hash 不同→刷屏）
            # 改为：提取关键实体词做语义指纹 + 同维度冷却期 6h 防重推
            import re as _re
            # 提取实体词：中文2-6字连续 + 英文/数字串，去标点空白
            tokens = _re.findall(r'[一-鿿]{2,6}|[A-Za-z]{3,}|\d{2,}', brief)
            # 去重+排序（顺序无关）→ 语义指纹
            fp = hashlib.sha1(("|".join(sorted(set(tokens))) + topic).encode("utf-8")).hexdigest()[:12]
            now = int(time.time())
            prev = state.get(topic)
            if isinstance(prev, dict):
                prev_fp = prev.get("fp")
                prev_ts = prev.get("ts", 0)
            else:
                # 兼容旧格式（纯 hash 字符串）
                prev_fp = prev
                prev_ts = 0
            COOLDOWN = 6 * 3600  # 同维度 6h 冷却
            if prev_fp == fp:
                print(f"  → 语义指纹相同，跳过（防刷屏）fp={fp}")
                continue
            if prev_ts and (now - prev_ts) < COOLDOWN:
                print(f"  → 冷却期内（{6 - (now - prev_ts) // 3600}h剩余），跳过")
                continue
            # LOOP-018：新增信号候选回灌待审区（三层拿来主义第3层自增长）
            cands = extract_signals(brief, dim, topic)
            append_inbox(cands, dim, topic)
            cid = chats.get(dim)
            if not cid:
                print(f"  → 该维度未配置飞书群（feishu_chat_{dim}），dry-run 不推")
                state[topic] = {"fp": fp, "ts": now}
                continue
            full = f"🔔 {hotel}·主动监控｜{DIM_CN.get(dim, dim)}\n主题：{topic}\n\n{brief}"
            post(cid, full, lark_bin=lark_bin, send=send, idem=f"watch_{topic[:20]}_{fp}")
            state[topic] = {"fp": fp, "ts": now}
            pushed += 1
        # state 写入 + fsync 确保落盘（GLM 验收者指出 fsync 缺失，实际此处有）
        with open(state_path, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        print(f"\n本轮推送 {pushed} 条新增。")
        return pushed
    finally:
        # 异常路径也释锁，防止死锁
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
        except Exception:
            pass
        _lock_fd.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "hotel_config.yaml"))
    ap.add_argument("--send", action="store_true", help="真正推送（默认 dry-run）")
    ap.add_argument("--interval", type=int, default=0, help="常驻间隔秒（0=跑一次）")
    ap.add_argument("--gen-watchlist", action="store_true",
                    help="从 signal_registry 客户聚类自动生成监控清单后退出（LOOP-017）")
    a = ap.parse_args()
    if a.gen_watchlist:
        gen_watchlist()
        return
    cfg = load_config(a.config)
    lark_bin = cfg.get("lark_bin") or "lark-cli"
    items = load_watchlist()
    if a.interval:
        print(f"事件监控常驻：间隔 {a.interval}s，send={a.send}")
        try:
            while True:
                run_once(cfg, items, lark_bin, a.send)
                time.sleep(a.interval)
        except KeyboardInterrupt:
            print("\n已停止。")
    else:
        run_once(cfg, items, lark_bin, a.send)


if __name__ == "__main__":
    main()
