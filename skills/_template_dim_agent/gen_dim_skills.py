#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
维度技能生成器：把「1 个模板 + hotel_config + capabilities」变成「10 个本酒店专属技能」。

这是从『10 个写死的瑞湾 prompt』走向『参数化动态 Agent』的关键：
- 方法论（双层拿来主义 / 机制提炼 / 红线）在模板里，与酒店无关
- 酒店专属数据（名/地理/群ID/路径）从 hotel_config.yaml 注入
- 维度名/关键词从 capabilities.json 注入

用法：
  python3 skills/_template_dim_agent/gen_dim_skills.py
（依赖仓库根 hotel_config.yaml；找不到则用 examples/hotel_config.example.yaml 的占位值）

输出：skills/_generated/market-<dim>-wb/SKILL.md（10 个），部署时复制到 ~/.workbuddy/skills/
"""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
TEMPLATE = os.path.join(HERE, "SKILL.md")
CAPS = os.path.join(REPO_ROOT, "skills", "market-intel-agent", "capabilities.json")
OUT_DIR = os.path.join(REPO_ROOT, "skills", "_generated")

# 维度序号（与 runtime_config.DIM_CN 对齐）
NUM_MAP = {
    "one": "(一)", "two": "(二)", "three": "(三)", "four": "(四)", "five": "(五)",
    "six": "(六)", "seven": "(七)", "potential": "(八)", "broad": "(九)", "tmc": "(十)",
}


def load_config():
    for p in (os.path.join(REPO_ROOT, "hotel_config.yaml"),
              os.path.join(REPO_ROOT, "examples", "hotel_config.example.yaml")):
        if os.path.exists(p):
            cfg = {}
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                k, v = line.split(":", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
            return cfg
    return {}


def main():
    cfg = load_config()
    hotel = cfg.get("hotel", "XX开元名都")
    geo = cfg.get("geo", "{geo}")
    anchor = cfg.get("anchor", "")
    harvest_dir = cfg.get("harvest_dir", "runtime/harvest")
    report_dir = cfg.get("report_dir", "runtime")
    dim_chat = {k.replace("feishu_chat_", ""): v
                for k, v in cfg.items() if k.startswith("feishu_chat_") and v}

    caps = json.load(open(CAPS, encoding="utf-8"))["capabilities"]
    tpl = open(TEMPLATE, encoding="utf-8").read()

    os.makedirs(OUT_DIR, exist_ok=True)
    n = 0
    for c in caps:
        key = c["key"]
        name = c["name"]
        kws = c.get("keywords", [])
        chat = dim_chat.get(key, f"<未配置：在 hotel_config 填 feishu_chat_{key}>")
        sub = {
            "{{HOTEL}}": hotel,
            "{{GEO}}": geo,
            "{{ANCHOR}}": anchor,
            "{{DIM_NAME}}": name,
            "{{DIM_KEY}}": key,
            "{{DIM_NUM}}": NUM_MAP.get(key, ""),
            "{{CHAT_ID}}": chat,
            "{{HARVEST_DIR}}": harvest_dir,
            "{{REPORT_DIR}}": report_dir,
            "{{DIM_KEYWORDS_COMMA}}": ", ".join(kws),
            "{{DIM_KEYWORDS_SPACE}}": " ".join(kws),
        }
        content = tpl
        for k, v in sub.items():
            content = content.replace(k, v)
        d = os.path.join(OUT_DIR, f"market-{key}-wb")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8").write(content)
        n += 1
        print(f"  ✅ market-{key}-wb  ({name}{NUM_MAP.get(key,'')}) chat={chat[:12]}...")

    print(f"\n🎉 已生成 {n} 个维度技能到 skills/_generated/")
    print("   部署：将 skills/_generated/* + skills/market-intel-agent + skills/market-iteration-toolkit 复制到 ~/.workbuddy/skills/")


if __name__ == "__main__":
    main()
