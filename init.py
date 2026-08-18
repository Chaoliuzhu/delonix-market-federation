#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键初始化 + 一键部署 + 引导补历史数据。

给「兄弟酒店」用的入口。克隆模板库后只需跑一条命令：

  python3 init.py --hotel "XX开元名都" --geo "城市/区" --mode A
  python3 init.py --hotel "XX酒店"     --geo "城市/区" --mode B \
        --chat-tmc oc_xxx --chat-two oc_yyy --chat-seven oc_zzz

本命令做六件事（真正一键，无需手敲 cp）：
  1. 从 examples/hotel_config.example.yaml 生成 hotel_config.yaml（填酒店名/地理/mode/群ID）
  2. 重置 runtime/signal_registry.json 为空（不带任何源酒店数据）
  3. 用参数化模板生成你酒店专属的 10 个维度技能 → skills/_generated/
  4. ★ 自动把技能部署进 WorkBuddy：~/.workbuddy/skills/
        （market-intel-agent 元 Agent + market-iteration-toolkit + 10 个维度技能）
  5. 在 runtime/harvest/ 生成「带说明的占位文件」，告诉你要补哪些历史资料
  6. ★ 结尾打印「引导补入历史数据」清单（P0/P1/P2 + 精确路径 + 不补的后果）

⚠️ 关键认知：系统能不能产出精准情报，取决于你喂的历史沉淀质量。
   跑完本命令系统即「能用」，但**补齐 P0 历史资料前，情报只跑框架、不精准**。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
EXAMPLE = os.path.join(ROOT, "examples", "hotel_config.example.yaml")
CFG_OUT = os.path.join(ROOT, "hotel_config.yaml")
CFG_SKILL = os.path.join(ROOT, "skills", "market-intel-agent", "hotel_config.yaml")
REG = os.path.join(ROOT, "runtime", "signal_registry.json")
HARVEST = os.path.join(ROOT, "runtime", "harvest")
GEN = os.path.join(ROOT, "skills", "_template_dim_agent", "gen_dim_skills.py")
DEFAULT_SKILLS = os.path.expanduser("~/.workbuddy/skills")


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


def init(hotel, geo, mode, chats, skills_dir, deploy=True, guide_only=False):
    if not guide_only:
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
        try:
            shutil.copy(CFG_OUT, CFG_SKILL)
        except Exception as e:
            print(f"  ⚠️ 复制配置到 skill 目录失败（router 将回退占位）：{e}")
        print(f"✅ 生成 hotel_config.yaml（hotel={hotel}, geo={geo}, mode={mode}）")

        # 2. 重置注册表（空壳）
        with open(REG, "w", encoding="utf-8") as f:
            json.dump({"updated_round": 0, "entries": []}, f, ensure_ascii=False, indent=2)
        print("✅ 重置 signal_registry.json 为空（去重基线待首轮建立）")

        # 3. 生成 10 个本酒店专属维度技能
        r = subprocess.run([sys.executable, GEN], cwd=ROOT, capture_output=True, text=True)
        if r.returncode == 0:
            for ln in r.stdout.strip().splitlines():
                if ln.startswith("  ✅"):
                    print(ln)
            print("✅ 已生成 10 个维度技能 → skills/_generated/")
        else:
            print(f"  ⚠️ 维度技能生成失败：{r.stderr[:300]}")

        # 4. 一键部署进 WorkBuddy
        if deploy:
            deploy_skills(skills_dir)

        # 5. 历史资料占位（带说明）
        make_harvest_placeholders()

    # 6. 引导补历史数据清单
    print_guide(hotel, mode, skills_dir, deployed=(deploy and not guide_only))


def deploy_skills(skills_dir):
    os.makedirs(skills_dir, exist_ok=True)
    print(f"\n📦 一键部署技能到 WorkBuddy：{skills_dir}")
    # 10 个维度技能（顶层 _generated）→ 直接放进 skills/
    gen = os.path.join(ROOT, "skills", "_generated")
    n = 0
    if os.path.isdir(gen):
        for d in sorted(os.listdir(gen)):
            src = os.path.join(gen, d)
            if os.path.isdir(src):
                dst = os.path.join(skills_dir, d)
                shutil.copytree(src, dst, dirs_exist_ok=True)
                n += 1
    # 元 Agent + 工具包
    for name in ("market-intel-agent", "market-iteration-toolkit"):
        src = os.path.join(ROOT, "skills", name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(skills_dir, name), dirs_exist_ok=True)
            n += 1
    print(f"  ✅ 已部署 {n} 个技能目录（元 Agent + 工具包 + 10 维），WorkBuddy 重启/刷新后即生效")
    print("  ℹ️ 之后在 WorkBuddy 对话里 @market-intel-agent 即可提问")


HARVEST_FILES = {
    "00_把历史资料放这里.md": """# 把历史资料放这个目录（runtime/harvest/）

本目录是「内层查证」的主数据源。系统能不能产出精准情报，取决于这里喂了什么。
按下方优先级把资料放进来（文件名随意，内容对就行），然后跑首轮即建立去重基线。

## P0 必交（不齐 = 系统只会跑框架，情报不精准）
- 历史协议单位 / 客户清单  → 放到 `protocol_units.txt`（每行一个单位名）
- 飞书市场/业务专群历史    → 在 hotel_config.yaml 填 `feishu_chat_<dim>` 群ID，
                              路径 B 下用 `python3 runtime/harvest_chat.py <群ID>` 拉取
- 酒店基础信息            → 已在 hotel_config.yaml（hotel/geo/anchor）

## P1 强烈建议（交齐 = 激活更多维度）
- 客源渠道台账   → `guest_source.csv`（渠道,占比,来源地）
- 餐饮/宴会/会议订单 → `fnb_orders.csv`（日期,类型,金额,客户）
- MICE 历史订单  → 可并入 fnb_orders.csv 或单独 mice_orders.csv

## P2 锦上添花（交齐 = 情报更精准）
- 竞品情报剪报 → `competitor.md`
- 会员权益/复购数据 → `member.csv`

> 没有本地数据，去重和迭代核验会退化成只靠注册表。资料越全，情报越准。
""",
    "protocol_units.txt": "# P0 必交：每行一个历史协议单位/客户名（去重基线靠它）\n"
                           "# 例：\n# 中航服天津分公司\n# 中海石油天津销售公司\n",
    "guest_source.csv": "渠道,占比,来源地\n# P1：客源渠道台账（删除本行，按实填）\n",
    "fnb_orders.csv": "日期,类型,金额,客户\n# P1：餐饮/宴会/会议订单（删除本行，按实填）\n",
    "competitor.md": "# P2：竞品情报剪报（对手酒店近期促销/套餐/价格动作）\n",
    "member.csv": "会员等级,复购次数,最近消费\n# P2：会员权益与复购数据\n",
}


def make_harvest_placeholders():
    os.makedirs(HARVEST, exist_ok=True)
    for fn, content in HARVEST_FILES.items():
        p = os.path.join(HARVEST, fn)
        if not os.path.exists(p):
            open(p, "w", encoding="utf-8").write(content)
    print(f"✅ runtime/harvest/ 已生成 {len(HARVEST_FILES)} 个「带说明的占位文件」（见 00_把历史资料放这里.md）")


def print_guide(hotel, mode, skills_dir, deployed):
    print("\n" + "=" * 58)
    print(f"🎯 【引导补入历史数据】—— 不补，情报就不精准")
    print("=" * 58)
    print(f"酒店：{hotel} ｜ 模式：{mode} ｜ 技能已部署：{'是 → '+skills_dir if deployed else '否（用 --no-deploy 跳过）'}")
    print()
    print("📌 P0 必交（缺一不可激活精准情报）：")
    print("  ① 历史协议单位/客户清单 → runtime/harvest/protocol_units.txt（每行一个）")
    print("  ② 飞书市场/业务专群历史 → hotel_config.yaml 填 feishu_chat_<dim> 群ID")
    if mode == "B":
        print("       路径 B：填完群ID后跑  python3 runtime/harvest_chat.py <群ID>  拉历史")
    print("  ③ 酒店基础信息 → 已在 hotel_config.yaml（hotel/geo/anchor 已填）")
    print()
    print("📌 P1 强烈建议（交齐激活更多维度）：")
    print("  ④ 客源渠道台账   → runtime/harvest/guest_source.csv")
    print("  ⑤ 餐饮/宴会/会议订单 → runtime/harvest/fnb_orders.csv")
    print()
    print("📌 P2 锦上添花（交齐情报更精准）：")
    print("  ⑥ 竞品情报剪报 → runtime/harvest/competitor.md")
    print("  ⑦ 会员复购数据 → runtime/harvest/member.csv")
    print()
    print("⚠️ 补齐 P0 前，系统能跑框架但情报不精准（去重退化、迭代核验弱）。")
    print("   补完资料跑首轮 → signal_registry.json 建立去重基线 → 情报才准。")
    print()
    print("▶ 下一步：")
    if deployed:
        print("  · WorkBuddy 刷新/重启后，对话 @market-intel-agent 提问即可（按查询动态路由维度）")
    else:
        print("  · 先部署技能：把 skills/_generated/* + skills/market-intel-agent + skills/market-iteration-toolkit 复制到 ~/.workbuddy/skills/")
    print("  · 把上面 P0~P2 资料放进 runtime/harvest/（按 00_把历史资料放这里.md 说明）")
    if mode == "B":
        print("  · 飞书开放平台建应用 + 配置 lark-cli（见 DEPLOY_GUIDE §3.1），把 bot 拉进各维度专群")
        print("  · 群里 @bot 提问即触发；或跑  python3 skills/market-intel-agent/event_watch.py --send  主动监控推送")
    print("=" * 58)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hotel", required=True, help="酒店名称")
    ap.add_argument("--geo", required=True, help="地理锚点，如 天津滨海")
    ap.add_argument("--mode", choices=["A", "B"], required=True, help="A=纯WorkBuddy / B=飞书交互")
    ap.add_argument("--chat-tmc", default="", help="路径B: TMC群 chat_id")
    ap.add_argument("--chat-two", default="", help="路径B: 企业协议群 chat_id")
    ap.add_argument("--chat-seven", default="", help="路径B: MICE群 chat_id")
    ap.add_argument("--skills-dir", default=DEFAULT_SKILLS, help="WorkBuddy 技能目录（默认 ~/.workbuddy/skills）")
    ap.add_argument("--no-deploy", action="store_true", help="不自动部署技能到 WorkBuddy（仅生成配置/技能/占位）")
    ap.add_argument("--guide", action="store_true", help="只打印「引导补历史数据」清单，不重新生成")
    a = ap.parse_args()
    chats = {}
    for dim, val in [("tmc", a.chat_tmc), ("two", a.chat_two), ("seven", a.chat_seven)]:
        if val:
            chats[dim] = val
    init(a.hotel, a.geo, a.mode, chats,
         skills_dir=os.path.expanduser(a.skills_dir),
         deploy=not a.no_deploy, guide_only=a.guide)


if __name__ == "__main__":
    main()
