#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书 @触发 接收器（路径 B · 交互 Agent 入口）

把「动态路由」接到飞书群里：人类在维度专群 @bot（或发消息），本接收器
轮询拉取新消息 → 检测触发 → 调用 router 路由 → 回复「命中哪些维度 + 下一步」。

设计要点：
  1. 默认 --dry-run：只打印「会怎么回复」，绝不真正发消息（对外发送需显式 --send）。
  2. 排除 sender_type=='app' 的消息（含每日信号推送），避免 bot 自己回复自己造成回环。
  3. 触发模式 trigger_mode：'mention'（默认，群内有人 @bot 才触发）或 'any_user'（任意人类消息）。
     精确匹配靠 hotel_config 的 bot_open_id / bot_name；未配置时，群内有 @mention 即视为 @我们。
  4. 用 message_id 做增量去重（receiver_state.json 记录每个群上次见到的最新消息）。
  5. LLM 语义路由可选（--use-llm），复用 router.py，失败自动降级关键词。

用法：
  python3 feishu_receiver.py --once --use-llm --dry-run        # 跑一轮（不发）
  python3 feishu_receiver.py --once --chat-id oc_xxx --dry-run # 只测一个群
  python3 feishu_receiver.py --self-test                       # 离线验证整条链路
  python3 feishu_receiver.py --send --interval 30              # 常驻循环 + 真正发消息
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from router import load_caps, route  # 复用动态路由器
from agent_loop import answer as loop_answer  # 执行闭环：路由→产出真实情报

# 维度中文名（与 runtime_config.DIM_CN / capabilities.json 对齐）
DIM_CN = {
    "mice": "会议会展(七)", "one": "休闲度假(一)", "two": "企业协议(二)",
    "three": "会员增购(三)", "four": "餐饮宴会(四)", "five": "长住公寓(五)",
    "six": "数字渠道(六)", "potentialsource": "潜在客源(八)",
    "broardsignal": "潜在广域(九)", "tmc": "TMC订单(十)",
}


def parse_cfg(path):
    cfg = {}
    if not os.path.exists(path):
        return cfg
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        if " #" in line:
            line = line.split(" #", 1)[0].rstrip()
        k, v = line.split(":", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def get_chats(cfg):
    return {dim: cfg[f"feishu_chat_{dim}"] for dim in DIM_CN if cfg.get(f"feishu_chat_{dim}")}


def fetch_messages(chat_id, lark_bin, page_size=20):
    cmd = [lark_bin, "im", "+chat-messages-list", "--as", "bot",
           "--chat-id", chat_id, "--page-size", str(page_size), "--format", "json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:
        sys.stderr.write(f"[fetch] {chat_id} 调用失败：{e}\n")
        return []
    if r.returncode != 0:
        return []
    try:
        d = json.loads(r.stdout)
    except Exception:
        return []
    if not d.get("ok"):
        return []
    return d.get("data", {}).get("messages", [])


def is_trigger(msg, cfg):
    sender = msg.get("sender") or {}
    if sender.get("sender_type") == "app":
        return False  # 排除 bot / 自动化消息（含每日信号推送）→ 防回环
    mentions = msg.get("mentions") or []
    mode = cfg.get("trigger_mode", "mention")
    if mode == "any_user":
        return True
    if not mentions:
        return False
    bid = cfg.get("bot_open_id")
    bname = cfg.get("bot_name")
    if bid or bname:
        for m in mentions:
            if (bid and m.get("id") == bid) or (bname and m.get("name") == bname):
                return True
        return False
    return True  # 群内只有我们的 bot，有 @mention 即视为 @我们


def extract_query(content):
    text = content or ""
    text = re.sub(r"@_user_\d+", "", text)        # 去掉 lark 提及占位
    text = re.sub(r"@[\u4e00-\u9fa5\w\-]+", "", text)  # 去掉 @名字
    return re.sub(r"\s{2,}", " ", text).strip()


def build_reply(rr, cfg):
    hotel = cfg.get("hotel", "本酒店")
    r = rr
    lines = [
        f"🤖 {hotel}·市场情报 Agent",
        f"查询：{r.get('query', '')}",
        f"路由：{r.get('mode')}（{'LLM语义' if r.get('llm') else '关键词'}）",
        "已为你定位维度：",
    ]
    for m in r.get("matched", []):
        sc = "、".join((m.get("scenes") or [])[:3]) or "—"
        lines.append(f"  • {m['name']}({m['key']}) — 适用场景：{sc}")
    lines.append(f"理由：{r.get('reason', '')}")
    lines.append("")
    lines.append("回复「执行 <维度名或编号>」或「全部执行」，我将拉取该维度最新外部情报"
                 "+历史沉淀去重后推送。")
    return "\n".join(lines)


def reply(chat_id, text, lark_bin, send=False, idem=None):
    if not send:
        print("    [DRY-RUN 不发送] 拟回复：")
        for ln in text.split("\n"):
            print("      " + ln)
        return
    cmd = [lark_bin, "im", "+messages-send", "--as", "bot",
           "--chat-id", chat_id, "--text", text]
    if idem:
        cmd += ["--idempotency-key", idem]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    ok = (r.returncode == 0) and ("\"ok\": true" in r.stdout or "ok\":true" in r.stdout)
    print("    发送：" + ("OK" if ok else "FAIL " + r.stdout[:120]))


def process_once(cfg, chats, lark_bin, state, use_llm, send, auto_answer=True):
    for dim, cid in chats.items():
        msgs = fetch_messages(cid, lark_bin)
        if not msgs:
            print(f"[{dim}] 无可读消息（群可能无权限/为空）")
            continue
        last = state.get(cid, "")
        first_run = not last  # 首跑只建基线，避免把历史积压当新消息回复
        for m in msgs:  # msgs 按 desc 排序（最新在前）
            mid = m.get("message_id")
            if last and mid == last:
                break  # 到达上次位置，更老的不再处理
            if first_run:
                continue
            if not is_trigger(m, cfg):
                continue
            q = extract_query(m.get("content", ""))
            if not q:
                continue
            print(f"\n[{dim}] 命中触发 @ {mid}：{q[:60]}")
            if auto_answer:
                # 执行闭环：直接产出真实情报简报并回复（dry-run/send 由 reply 控制）
                text = loop_answer(q, geo=cfg.get("geo"), use_llm=use_llm)
                reply(cid, text, lark_bin, send=send, idem=mid)
            else:
                rr = route(q, geo=cfg.get("geo"), use_llm=use_llm)
                reply(cid, build_reply(rr, cfg), lark_bin, send=send, idem=mid)
        if msgs:
            state[cid] = msgs[0].get("message_id")  # 记录最新消息用于增量
    return state


def self_test(cfg):
    print("===== 离线自测：触发 → 路由 → 回复 整条链路 =====")
    fake = {
        "sender": {"sender_type": "user", "name": "张三"},
        "mentions": [{"id": "ou_bot123", "key": "@_user_1", "name": "市场情报bot"}],
        "content": "@市场情报bot 最近天津央企有什么会议住宿和培训需求？",
        "message_id": "test_msg_1",
    }
    cfg_test = dict(cfg, trigger_mode="mention", bot_name="市场情报bot")
    print("is_trigger:", is_trigger(fake, cfg_test))
    q = extract_query(fake["content"])
    print("extract_query:", repr(q))
    rr = route(q, geo=cfg.get("geo"), use_llm=False)
    print("route:", rr["mode"], [m["key"] for m in rr["matched"]], "llm=" + str(rr.get("llm")))
    print("---- 生成回复预览 ----")
    print(build_reply(rr, cfg_test))
    print("===== 自测结束 =====")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "hotel_config.yaml"))
    ap.add_argument("--once", action="store_true", help="只跑一轮不循环")
    ap.add_argument("--send", action="store_true", help="真正发送（默认 dry-run）")
    ap.add_argument("--use-llm", action="store_true", help="启用 LLM 语义路由")
    ap.add_argument("--no-answer", action="store_true",
                    help="仅回复路由结果，不自动产出情报（默认触发即产出）")
    ap.add_argument("--chat-id", default=None, help="只测单个群（dim 显示为 unknown）")
    ap.add_argument("--interval", type=int, default=30, help="循环间隔秒")
    ap.add_argument("--self-test", action="store_true", help="离线验证链路")
    a = ap.parse_args()

    cfg = parse_cfg(a.config)
    lark_bin = cfg.get("lark_bin") or "lark-cli"

    if a.self_test:
        self_test(cfg)
        return

    chats = get_chats(cfg)
    if a.chat_id:
        chats = {"unknown": a.chat_id}

    if not chats:
        print("⚠️ hotel_config.yaml 中没有任何 feishu_chat_<dim> 配置了群 ID，"
              "路径 B 无法工作。请先填写后再运行。")
        return

    state_path = os.path.join(HERE, "receiver_state.json")
    state = json.load(open(state_path)) if os.path.exists(state_path) else {}

    print(f"飞书接收器启动：chats={list(chats.keys())} send={a.send} use_llm={a.use_llm}")
    if a.once:
        state = process_once(cfg, chats, lark_bin, state, a.use_llm, a.send,
                             auto_answer=not a.no_answer)
        json.dump(state, open(state_path, "w"), ensure_ascii=False, indent=2)
        return
    try:
        while True:
            state = process_once(cfg, chats, lark_bin, state, a.use_llm, a.send,
                                 auto_answer=not a.no_answer)
            json.dump(state, open(state_path, "w"), ensure_ascii=False, indent=2)
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
