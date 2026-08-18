#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
运行时配置加载器（去硬编码的核心）。

所有 runtime 脚本（publish_signals / harvest_* / dedup2 / ...）统一从这里取：
  - 飞书 bin / python bin 路径（自动探测，可在 hotel_config 覆盖）
  - 多维表格 token / table
  - 每个维度的专群 chat_id（由 feishu_chat_<dim> 拼出 DIM_CHAT）
  - 本地沉淀池 / 注册表 / 报告目录（相对仓库根解析）
  - 酒店名 / 地理锚点（供卡片文案使用）

这样「瑞湾专属」的 10 维执行管线变成「某兄弟酒店专属」的空壳，
只需一份 hotel_config.yaml 即可切换，无需改任何脚本代码。

定位 hotel_config.yaml 的顺序：
  1. 仓库根 / hotel_config.yaml
  2. 本脚本所在 dir / hotel_config.yaml
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

# 维度中文名（与 capabilities.json 对齐，与酒店无关，可直接复用）
DIM_CN = {
    "mice": "会议会展(七)", "one": "休闲度假(一)", "two": "企业协议(二)",
    "three": "会员增购(三)", "four": "餐饮宴会(四)", "five": "长住公寓(五)",
    "six": "数字渠道(六)", "potentialsource": "潜在客源(八)",
    "broardsignal": "潜在广域(九)", "tmc": "TMC订单(十)",
}


def _find_config():
    for p in (os.path.join(REPO_ROOT, "hotel_config.yaml"),
              os.path.join(HERE, "hotel_config.yaml")):
        if os.path.exists(p):
            return p
    return None


def _parse(path):
    cfg = {}
    if not path:
        return cfg
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        if " #" in line:  # 去掉行内注释，避免污染取值
            line = line.split(" #", 1)[0].rstrip()
        k, v = line.split(":", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def load():
    cfg = _parse(_find_config())

    # bin 路径：配置优先，否则自动探测
    python_bin = cfg.get("python_bin") or sys.executable or "python3"
    lark_bin = cfg.get("lark_bin") or "lark-cli"

    # 多维表格
    base_token = cfg.get("bitable_token", "")
    table_id = cfg.get("bitable_table", "")

    # 维度专群：feishu_chat_<dim> -> DIM_CHAT
    dim_chat = {}
    for dim in DIM_CN:
        cid = cfg.get(f"feishu_chat_{dim}", "")
        if cid:
            dim_chat[dim] = cid

    # 本地路径（相对仓库根解析）
    def resolve(p, default):
        p = p or default
        return p if os.path.isabs(p) else os.path.join(REPO_ROOT, p)
    harvest_dir = resolve(cfg.get("harvest_dir"), "runtime/harvest")
    registry = resolve(cfg.get("registry"), "runtime/signal_registry.json")
    report_dir = resolve(cfg.get("report_dir"), "runtime")

    return {
        "hotel": cfg.get("hotel", "UNSET"),
        "geo": cfg.get("geo", "{geo}"),
        "anchor": cfg.get("anchor", ""),
        "city": cfg.get("city", ""),
        "python_bin": python_bin,
        "lark_bin": lark_bin,
        "base_token": base_token,
        "table_id": table_id,
        "dim_chat": dim_chat,
        "dim_cn": DIM_CN,
        "harvest_dir": harvest_dir,
        "registry": registry,
        "report_dir": report_dir,
        "raw": cfg,
    }


# 全局单例（脚本直接 import 后用 CFG["xxx"] 取值）
CFG = load()

if __name__ == "__main__":
    import json
    print(json.dumps(CFG, ensure_ascii=False, indent=2))
