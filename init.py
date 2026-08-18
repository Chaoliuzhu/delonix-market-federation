#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键初始化：把「瑞湾专属」的联邦系统变成「某兄弟酒店专属」的空壳。

做四件事：
  1. 从 examples/hotel_config.example.yaml 生成 hotel_config.yaml（填入酒店名/地理/mode）
  2. 重置 runtime/signal_registry.json 为空（不带瑞湾数据）
  3. 建 runtime/harvest/ 目录 + 资料占位文件（协议单位/客源/订单）
  4. 按 mode 输出后续步骤（路径 A 纯 WorkBuddy / 路径 B 飞书交互）

用法：
  python3 init.py --hotel "XX酒店" --geo "天津滨海" --mode A
  python3 init.py --hotel "XX酒店" --geo "天津滨海" --mode B --chat-tmc oc_xxx --chat-two oc_yyy
"""
import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
EXAMPLE = os.path.join(ROOT, "examples", "hotel_config.example.yaml")
CFG_OUT = os.path.join(ROOT, "hotel_config.yaml")
CFG_SKILL = os.path.join(ROOT, "skills", "market-intel-agent", "hotel_config.yaml")
REG = os.path.join(ROOT, "runtime", "signal_registry.json")
HARVEST = os.path.join(ROOT, "runtime", "harvest")
GEN = os.path.join(ROOT, "skills", "_template_dim_agent", "gen_dim_skills.py")


def parse_example(path):
    cfg = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        if " #" in line:  # 去掉行内注释，避免污染取值
            line = line.split(" #", 1)[0].rstrip()
        k, v = line.split(":", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def init(hotel, geo, mode, chats):
    os.makedirs(HARVEST, exist_ok=True)

    # 1. 生成配置
    cfg = parse_example(EXAMPLE)
    cfg["hotel"] = hotel
    cfg["geo"] = geo
    cfg["mode"] = mode
    if mode == "B":
        for dim, cid in chats.items():
            cfg[f"feishu_chat_{dim}"] = cid
    lines = [f'# 由 init.py 生成（mode={mode}）', ""]
    for k, v in cfg.items():
        lines.append(f'{k}: "{v}"')
    with open(CFG_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    # 同步一份到动态 Agent skill 目录（router.py 默认在自身目录找配置）
    try:
        shutil.copy(CFG_OUT, CFG_SKILL)
    except Exception as e:
        print(f"  ⚠️ 复制配置到 skill 目录失败（router 将回退占位）：{e}")
    print(f"✅ 生成 hotel_config.yaml（hotel={hotel}, geo={geo}, mode={mode}）")

    # 2. 重置注册表（空壳，不带瑞湾数据）
    with open(REG, "w", encoding="utf-8") as f:
        json.dump({"updated_round": 0, "entries": []}, f, ensure_ascii=False, indent=2)
    print("✅ 重置 signal_registry.json 为空（去重基线待首轮建立）")

    # 2.5 生成 10 个本酒店专属维度技能（模板 + 配置注入）
    import subprocess
    r = subprocess.run([sys.executable, GEN], cwd=ROOT, capture_output=True, text=True)
    if r.returncode == 0:
        for ln in r.stdout.strip().splitlines():
            if ln.startswith("  ✅"):
                print(ln)
        print(f"✅ 已生成 10 个维度技能到 skills/_generated/")
    else:
        print(f"  ⚠️ 维度技能生成失败：{r.stderr[:300]}")

    # 3. 资料占位
    placeholders = {
        "protocol_units.txt": "# 每行一个历史协议单位/客户名（P0 必交）\n",
        "source_channels.csv": "渠道,占比,来源地\n",
        "fnb_orders.csv": "日期,类型,金额,客户\n",
    }
    for fn, content in placeholders.items():
        p = os.path.join(HARVEST, fn)
        if not os.path.exists(p):
            open(p, "w", encoding="utf-8").write(content)
    print(f"✅ 建 runtime/harvest/ 并放资料占位（共 {len(placeholders)} 个）")

    # 4. 后续步骤
    print("\n" + "=" * 50)
    print("【部署】把以下技能目录复制到 ~/.workbuddy/skills/：")
    print("  skills/_generated/*  （10 个本酒店维度技能，刚生成）")
    print("  skills/market-intel-agent/  （动态路由元 Agent）")
    print("  skills/market-iteration-toolkit/  （迭代工具包）")
    if mode == "A":
        print("\n【路径 A · 纯 WorkBuddy 独立 Agent】后续：")
        print("  1. 复制上述技能到 ~/.workbuddy/skills/")
        print("  2. 在 WorkBuddy 对话里 @market-intel-agent 提问即可（按查询动态路由维度）")
        print("  3. 历史资料已放 runtime/harvest/，跑首轮即建去重基线")
    else:
        print("\n【路径 B · 飞书交互 Agent】后续：")
        print("  1. 飞书开放平台建应用，配置 lark-cli（见 DEPLOY_GUIDE §3.1）")
        print("  2. 在 hotel_config.yaml 补齐各维度 feishu_chat_<dim>（把 bot 拉进群先验证）")
        print("  3. cd runtime && python3 harvest_chat.py <群ID> out.json 拉历史")
        print("  4. python3 harvest_index.py build 建索引")
        print("  5. 飞书群 @bot 提问即触发（或跑定时 cron 全量扫描）")
    print("=" * 50)
    print("\n⚠️ 记得补齐 P0 资料（协议单位/飞书群/酒店信息）才能激活精准情报。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hotel", required=True, help="酒店名称")
    ap.add_argument("--geo", required=True, help="地理锚点，如 天津滨海")
    ap.add_argument("--mode", choices=["A", "B"], required=True, help="A=纯WorkBuddy / B=飞书交互")
    ap.add_argument("--chat-tmc", default="", help="路径B: TMC群 chat_id")
    ap.add_argument("--chat-two", default="", help="路径B: 企业协议群 chat_id")
    ap.add_argument("--chat-seven", default="", help="路径B: MICE群 chat_id")
    a = ap.parse_args()
    chats = {}
    for dim, val in [("tmc", a.chat_tmc), ("two", a.chat_two), ("seven", a.chat_seven)]:
        if val:
            chats[dim] = val
    init(a.hotel, a.geo, a.mode, chats)


if __name__ == "__main__":
    main()
