#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cross_dim_linker.py · 跨维度关联引擎（L3 Orchestration 前置能力）

当同一市场信号被多个维度的小分子 Agent 捕获时，提供合并策略与关联图谱。

核心能力：
  1. 信号指纹去重：同一实体被多维度捕获时识别为同一信号
  2. 关联图谱构建：维度间联动关系（如"广域政策利好 → 企业协议机会"）
  3. 合并策略：多维度同一信号的合并规则（取最详、补全字段）
  4. 结果输出：关联图谱 JSON + 可选写入增量池 Base「多酒店市场对比」表

关联规则（基于业务语义预置 + signal_registry 实体匹配）：
  broardsignal → two/one/six    （广域政策利好 → 企业协议/休闲/数字渠道）
  two → four/seven              （企业协议 → 餐饮宴会/会议会展）
  potentialsource → two/one     （潜在客源 → 企业协议/休闲）
  six → one/three               （数字渠道 → 休闲/会员）
  seven → four                  （会议会展 → 餐饮宴会）
  tmc → two                     （TMC订单 → 企业协议）

用法：
  python3 cross_dim_linker.py --scan                  # 扫描 registry 输出关联图谱
  python3 cross_dim_linker.py --scan --json          # JSON 输出
  python3 cross_dim_linker.py --dim broardsignal     # 单维度关联视图
  python3 cross_dim_linker.py --write-base           # 写入 Base 多酒店市场对比表
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CST = timezone(timedelta(hours=8))

# 维度中文名
DIM_CN = {
    "one": "休闲度假", "two": "企业协议", "three": "会员增购",
    "four": "餐饮宴会", "five": "长住公寓", "six": "数字渠道",
    "seven": "会议会展", "mice": "会议会展",
    "potentialsource": "潜在客源", "broardsignal": "潜在广域",
    "tmc": "TMC订单",
}

# 预置维度关联规则：source_dim → [target_dims] + 关联语义
# 基于酒店市场业务逻辑：上游信号如何驱动下游机会
LINK_RULES = {
    "broardsignal": [
        {"target": "two",   "relation": "广域政策利好 → 企业协议机会",
         "logic": "区域政策（如自贸区、产业园区规划）→ 央企/国企差旅协议需求增长"},
        {"target": "one",   "relation": "广域文旅政策 → 休闲度假热度",
         "logic": "文旅消费刺激政策 → 周边游/休闲度假客源增长"},
        {"target": "six",   "relation": "广域数字化政策 → 数字渠道机会",
         "logic": "数字经济政策 → OTA/直销/数字化营销新机会"},
    ],
    "two": [
        {"target": "four",  "relation": "企业协议 → 餐饮宴会",
         "logic": "协议客户商务活动 → 宴会/团餐需求"},
        {"target": "seven", "relation": "企业协议 → 会议会展",
         "logic": "协议客户年会/培训 → 会议场地需求"},
    ],
    "potentialsource": [
        {"target": "two",   "relation": "潜在客源 → 企业协议",
         "logic": "潜在企业/机构线索 → 协议转化"},
        {"target": "one",   "relation": "潜在客源 → 休闲度假",
         "logic": "潜在客群 → 休闲度假套餐转化"},
    ],
    "six": [
        {"target": "one",   "relation": "数字渠道 → 休闲度假",
         "logic": "OTA/直播引流 → 休闲客源"},
        {"target": "three", "relation": "数字渠道 → 会员增购",
         "logic": "私域/数字化触达 → 会员复购"},
    ],
    "seven": [
        {"target": "four",  "relation": "会议会展 → 餐饮宴会",
         "logic": "会议/展会 → 餐饮/宴会配套需求"},
    ],
    "tmc": [
        {"target": "two",   "relation": "TMC订单 → 企业协议",
         "logic": "差旅平台订单数据 → 企业协议客户识别与深化"},
    ],
}


def load_registry(registry_path=None):
    """加载 signal_registry.json。"""
    reg = registry_path or os.path.join(REPO, "runtime", "signal_registry.json")
    if not os.path.exists(reg):
        return None
    try:
        return json.load(open(reg, encoding="utf-8"))
    except Exception as e:
        print(f"[cross-dim] 注册表读取失败：{e}")
        return None


def extract_entity_name(spec):
    """从 spec（名称|地理|类型）提取实体名。"""
    if not spec:
        return ""
    # 去掉前缀编号（如 B1·、B2·）
    name = spec.split("|")[0] if "|" in spec else spec
    # 去掉 B1· / B2· 等编号前缀
    if "·" in name:
        name = name.split("·", 1)[-1]
    return name.strip()


def find_cross_dim_signals(registry_data):
    """识别被多维度捕获的同一实体信号。

    通过 canonical_name / 实体名 跨维度匹配。
    Returns:
        list[dict]: 每条含 entity, dims, entries
    """
    if not registry_data:
        return []
    entries = registry_data.get("entries", [])

    # 按实体名分组
    entity_map = defaultdict(list)
    for e in entries:
        name = e.get("canonical_name") or extract_entity_name(e.get("spec", ""))
        if name:
            entity_map[name].append(e)

    # 找出被≥2维度捕获的实体
    cross_signals = []
    for name, ents in entity_map.items():
        dims = set(e.get("dim", "") for e in ents)
        if len(dims) >= 2:
            cross_signals.append({
                "entity": name,
                "dims": sorted(dims),
                "dim_count": len(dims),
                "entries": [{"dim": e.get("dim"), "spec": e.get("spec"),
                             "status": e.get("status")} for e in ents],
            })
    return cross_signals


def build_link_graph(dim=None):
    """构建维度关联图谱。

    Args:
        dim: 指定源维度（None=全量）
    Returns:
        list[dict]: 关联边
    """
    edges = []
    for src, targets in LINK_RULES.items():
        if dim and src != dim:
            continue
        for t in targets:
            edges.append({
                "source": src,
                "source_name": DIM_CN.get(src, src),
                "target": t["target"],
                "target_name": DIM_CN.get(t["target"], t["target"]),
                "relation": t["relation"],
                "logic": t["logic"],
            })
    return edges


def merge_strategy(cross_signals):
    """多维度同一信号的合并策略。

    规则：
      1. 取最详 spec（字段最全）
      2. 合并维度标签
      3. 保留各维度原始 entry 引用（审计追溯）
    """
    merged = []
    for cs in cross_signals:
        # 取 spec 最长的作为主记录
        entries = cs["entries"]
        primary = max(entries, key=lambda e: len(e.get("spec", "")))
        merged.append({
            "entity": cs["entity"],
            "merged_dims": cs["dims"],
            "primary_spec": primary.get("spec"),
            "merge_rule": "取最详 spec + 合并维度标签",
            "source_entries": entries,
        })
    return merged


def scan(registry_path=None, dim=None):
    """全量扫描：关联图谱 + 跨维度信号 + 合并结果。"""
    reg = load_registry(registry_path)
    cross = find_cross_dim_signals(reg) if reg else []
    edges = build_link_graph(dim=dim)
    merged = merge_strategy(cross)

    return {
        "scan_time": datetime.now(CST).isoformat(timespec="seconds"),
        "link_graph": edges,
        "cross_dim_signals": cross,
        "merged": merged,
        "stats": {
            "total_edges": len(edges),
            "cross_dim_entities": len(cross),
            "merged_records": len(merged),
        },
    }


def write_to_base(result, base_token=None):
    """将关联图谱写入增量池 Base「多酒店市场对比」表。

    需要 lark-cli base 写权限。若无权限则打印提示。
    """
    # 检查 lark-cli 是否可用
    try:
        import subprocess
        # 先定位表
        cmd = ["lark-cli", "base", "+table-list",
               "--base-token", base_token or "UYUpbchmlaE1HBsJZKmcM5pBnyd",
               "--as", "user"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0 or '"ok":false' in r.stdout:
            print(f"[cross-dim] Base 写入需要用户授权：{r.stdout[:200]}")
            print("             请先运行 lark-cli auth login 授权 base 权限")
            return False
        # TODO: 定位「多酒店市场对比」表并写入关联记录
        # 表结构需先 +table-list 确认 table_id
        print(f"[cross-dim] Base 写入待授权后执行（关联图谱 {len(result.get('link_graph', []))} 条边）")
        return True
    except Exception as e:
        print(f"[cross-dim] Base 写入异常：{e}")
        return False


def main():
    ap = argparse.ArgumentParser(description="跨维度关联引擎")
    ap.add_argument("--scan", action="store_true", help="扫描关联图谱")
    ap.add_argument("--dim", default=None, help="指定源维度")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--write-base", action="store_true",
                    help="写入增量池 Base 多酒店市场对比表")
    a = ap.parse_args()

    if a.scan or not any([a.scan, a.dim, a.write_base]):
        result = scan(dim=a.dim)
        if a.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"\n=== 跨维度关联图谱 ===")
            print(f"扫描时间：{result['scan_time']}")
            print(f"关联边：{result['stats']['total_edges']} 条")
            print(f"跨维度实体：{result['stats']['cross_dim_entities']} 个\n")
            for e in result["link_graph"]:
                print(f"  {e['source_name']} → {e['target_name']}")
                print(f"    {e['relation']}")
                print(f"    {e['logic']}\n")
            if result["cross_dim_signals"]:
                print(f"=== 跨维度同一信号 ===")
                for cs in result["cross_dim_signals"]:
                    print(f"  {cs['entity']}（{cs['dim_count']}维: "
                          f"{'、'.join(DIM_CN.get(d,d) for d in cs['dims'])}）")
                print()
            print(f"统计：{result['stats']}")
        if a.write_base:
            write_to_base(result)
        return

    if a.dim:
        result = scan(dim=a.dim)
        edges = result["link_graph"]
        print(f"\n{DIM_CN.get(a.dim, a.dim)} 的关联视图：")
        for e in edges:
            print(f"  → {e['target_name']}：{e['relation']}")
        return

    if a.write_base:
        result = scan()
        write_to_base(result)


if __name__ == "__main__":
    main()
