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
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from router import load_config
from agent_loop import watch, post, DIM_CN

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


def run_once(cfg, items, lark_bin, send):
    hotel = cfg.get("hotel", "本酒店")
    geo = cfg.get("geo", "未指定")
    chats = {d: cfg[f"feishu_chat_{d}"] for d in DIM_CN if cfg.get(f"feishu_chat_{d}")}
    state_path = os.path.join(HERE, "event_watch_state.json")
    state = json.load(open(state_path)) if os.path.exists(state_path) else {}
    pushed = 0
    for it in items:
        dim = it["dim"]
        topic = it["topic"]
        print(f"\n[监控] {DIM_CN.get(dim, dim)}｜{topic}")
        brief = watch(topic, dim, hotel, geo)
        print("  LLM：" + brief[:80].replace("\n", " "))
        if not brief.startswith("【新增】"):
            print("  → 无新增，跳过")
            continue
        h = hashlib.sha1((topic + brief).encode("utf-8")).hexdigest()[:12]
        if state.get(topic) == h:
            print("  → 与上次相同，跳过（防刷屏）")
            continue
        cid = chats.get(dim)
        if not cid:
            print(f"  → 该维度未配置飞书群（feishu_chat_{dim}），dry-run 不推")
            state[topic] = h
            continue
        full = f"🔔 {hotel}·主动监控｜{DIM_CN.get(dim, dim)}\n主题：{topic}\n\n{brief}"
        post(cid, full, lark_bin=lark_bin, send=send, idem=f"watch_{topic[:20]}_{h}")
        state[topic] = h
        pushed += 1
    json.dump(state, open(state_path, "w"), ensure_ascii=False, indent=2)
    print(f"\n本轮推送 {pushed} 条新增。")
    return pushed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "hotel_config.yaml"))
    ap.add_argument("--send", action="store_true", help="真正推送（默认 dry-run）")
    ap.add_argument("--interval", type=int, default=0, help="常驻间隔秒（0=跑一次）")
    a = ap.parse_args()
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
