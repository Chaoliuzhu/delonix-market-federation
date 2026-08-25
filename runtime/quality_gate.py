#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quality_gate.py · 质量门标准化模块（L2 Observability 基础设施）

把 27 轮迭代中演化出的多种质量门格式统一为标准形态：
  标准格式：X✅/Y⚠️/Z❌   （pass / warn / fail）
  quality_score = pass×1.0 + warn×0.5 + fail×0
  quality_grade = A(≥10) / B(8-9) / C(6-7) / D(≤5)

历史格式兼容（backfill）：
  - "8/8 pass"          → 8✅/0⚠️/0❌
  - "v2 · 8维 · 0 fail" → 8✅/0⚠️/0❌（0 fail 视为全 pass，剩余维度补 warn）
  - "9✅/5⚠️/0❌"        → 原样解析
  - "7/7"              → 7✅/0⚠️/0❌
  - {"pass":8,"warn":1,"fail":0} → dict 形式

用法：
  python3 quality_gate.py --text "9✅/5⚠️/0❌"          # 解析单条
  python3 quality_gate.py --backfill --round 27        # 回填某轮全维度
  python3 quality_gate.py --health                    # 10 维健康度概览
  python3 quality_gate.py --text "8/8 pass" --json     # JSON 输出
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# ── 维度元数据（与 capabilities.json / agent_loop.DIM_CN 对齐）──
DIM_META = {
    "one":            {"name": "休闲度假",   "agent_id": "market-one-wb"},
    "two":            {"name": "企业协议",   "agent_id": "market-two-wb"},
    "three":          {"name": "会员增购",   "agent_id": "market-three-wb"},
    "four":           {"name": "餐饮宴会",   "agent_id": "market-four-wb"},
    "five":           {"name": "长住公寓",   "agent_id": "market-five-wb"},
    "six":            {"name": "数字渠道",   "agent_id": "market-six-wb"},
    "seven":          {"name": "会议会展",   "agent_id": "market-seven-wb"},
    "mice":           {"name": "会议会展",   "agent_id": "market-seven-wb"},
    "potentialsource":{"name": "潜在客源",   "agent_id": "market-potential-wb"},
    "broardsignal":   {"name": "潜在广域",   "agent_id": "market-broad-wb"},
    "tmc":            {"name": "TMC订单",    "agent_id": "market-tmc-wb"},
}


class QualityGate:
    """标准化质量门。

    内部统一存储 (pass, warn, fail) 三元组，对外提供标准格式字符串、
    分数和等级。所有历史格式的解析都走 from_text / from_dict 工厂方法。
    """

    def __init__(self, passed=0, warn=0, fail=0):
        self.passed = int(passed)
        self.warn = int(warn)
        self.fail = int(fail)

    # ── 标准格式 ──
    def __str__(self):
        return f"{self.passed}✅/{self.warn}⚠️/{self.fail}❌"

    def __repr__(self):
        return f"QualityGate({self})"

    def to_dict(self):
        return {"pass": self.passed, "warn": self.warn, "fail": self.fail,
                "score": self.score(), "grade": self.grade(), "format": str(self)}

    # ── 量化 ──
    def total(self):
        return self.passed + self.warn + self.fail

    def score(self):
        """quality_score = pass×1.0 + warn×0.5 + fail×0"""
        return self.passed * 1.0 + self.warn * 0.5 + self.fail * 0

    def grade(self):
        """quality_grade = A(≥10) / B(8-9) / C(6-7) / D(≤5)"""
        s = self.score()
        if s >= 10:
            return "A"
        if s >= 8:
            return "B"
        if s >= 6:
            return "C"
        return "D"

    # ── 工厂方法：解析历史格式 ──
    @classmethod
    def from_text(cls, text):
        """从文本解析质量门，兼容多种历史格式。

        支持格式：
          "9✅/5⚠️/0❌"        标准格式
          "8/8 pass"          分数/总分 pass
          "v2 · 8维 · 0 fail"  v2 格式（N维 · 0 fail）
          "7/7"               分数/总分（全 pass 推断）
          "8维 0❌"            简写
          "9/9/0"             pass/warn/fail 三数
        """
        if not text or not isinstance(text, str):
            return cls()
        t = text.strip()

        # 1. 标准格式：9✅/5⚠️/0❌（含 emoji 变体 + VS16 选择器）
        # ⚠️ = U+26A0 + U+FE0F，字符类 [] 会拆 VS16，故用字面 emoji + 可选 ️
        m = re.search(
            r'(\d+)\s*✅\s*/\s*(\d+)\s*⚠️?\s*/\s*(\d+)\s*❌',
            t, re.I)
        if m:
            return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        # 1b. 变体：用 [✓] / [!] / [x] 等纯 ASCII 标记
        m = re.search(
            r'(\d+)\s*[✓✔]\s*/\s*(\d+)\s*[!]\s*/\s*(\d+)\s*[x✗]',
            t, re.I)
        if m:
            return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)))

        # 1b. 标准格式无 emoji：9/5/0（三数斜杠，第三数小且含 fail 语义）
        m3 = re.match(r'^\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)\s*$', t)
        if m3:
            a, b, c = int(m3.group(1)), int(m3.group(2)), int(m3.group(3))
            # 若三数之和约等于 14（10维+4层去重 或类似），视为 pass/warn/fail
            if a + b + c <= 20:
                return cls(a, b, c)

        # 2. "8/8 pass" 或 "8维 pass"
        m = re.search(r'(\d+)\s*/\s*(\d+)\s*pass', t, re.I)
        if m:
            p, total = int(m.group(1)), int(m.group(2))
            return cls(p, total - p, 0)

        m = re.search(r'(\d+)\s*维\s*pass', t, re.I)
        if m:
            return cls(int(m.group(1)), 0, 0)

        # 3. "v2 · 8维 · 0 fail"  →  8✅/0⚠️/0❌（0 fail 视为全 pass）
        m = re.search(r'(\d+)\s*维\s*[·-]?\s*(\d+)\s*fail', t, re.I)
        if m:
            dims, fails = int(m.group(1)), int(m.group(2))
            return cls(dims - fails, 0, fails)

        m = re.search(r'(\d+)\s*维.*?(\d+)\s*fail', t, re.I)
        if m:
            dims, fails = int(m.group(1)), int(m.group(2))
            return cls(dims - fails, 0, fails)

        # 4. "8维 0❌" 简写
        m = re.search(r'(\d+)\s*(?:维|维度)?\s*0\s*[❌✗x]', t, re.I)
        if m:
            return cls(int(m.group(1)), 0, 0)

        # 5. "7/7" 纯分数/总分
        m = re.match(r'^\s*(\d+)\s*/\s*(\d+)\s*$', t)
        if m:
            p, total = int(m.group(1)), int(m.group(2))
            return cls(p, total - p, 0)

        # 6. 单数字 "8" 视为 8 pass
        m = re.match(r'^\s*(\d+)\s*$', t)
        if m:
            return cls(int(m.group(1)), 0, 0)

        return cls()

    @classmethod
    def from_dict(cls, d):
        """从 dict 解析：{"pass":8,"warn":1,"fail":0}"""
        if not isinstance(d, dict):
            return cls()
        return cls(d.get("pass", 0), d.get("warn", 0), d.get("fail", 0))

    @classmethod
    def parse(cls, raw):
        """自动识别类型并解析。"""
        if isinstance(raw, (dict,)):
            return cls.from_dict(raw)
        if isinstance(raw, str):
            return cls.from_text(raw)
        if isinstance(raw, (list, tuple)) and len(raw) >= 3:
            return cls(raw[0], raw[1], raw[2])
        return cls()


# ── 历史回填 ──

def backfill_registry(round_num=None, registry_path=None):
    """回填 signal_registry 中的历史质量门记录为标准格式。

    扫描 signal_registry.json，对每条 entry 的 quality 字段（若存在）
    统一解析为 QualityGate 标准格式，写回 entry["quality_std"]。
    不覆盖原始 quality 字段（保留审计痕迹）。

    Args:
        round_num: 指定轮次回填（None=全量）
        registry_path: 注册表路径（默认 runtime/signal_registry.json）
    Returns:
        (total, backfilled) — 扫描条数 / 回填条数
    """
    reg = registry_path or os.path.join(REPO, "runtime", "signal_registry.json")
    if not os.path.exists(reg):
        print(f"[backfill] 注册表不存在：{reg}")
        return 0, 0
    try:
        d = json.load(open(reg, encoding="utf-8"))
    except Exception as e:
        print(f"[backfill] 读取失败：{e}")
        return 0, 0

    entries = d.get("entries", [])
    total = 0
    backfilled = 0
    for e in entries:
        if round_num is not None and e.get("round") != round_num:
            continue
        total += 1
        raw = e.get("quality") or e.get("quality_gate") or e.get("gate")
        if raw is None:
            continue
        qg = QualityGate.parse(raw)
        e["quality_std"] = qg.to_dict()
        # 标记是否发生了格式转换（原始非标准格式）
        if isinstance(raw, str) and "✅" not in raw:
            e["quality_backfilled"] = True
            backfilled += 1
        elif isinstance(raw, dict):
            e["quality_backfilled"] = True
            backfilled += 1

    # 写回
    with open(reg, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(f"[backfill] 扫描 {total} 条，回填 {backfilled} 条 → {reg}")
    return total, backfilled


def health_overview(registry_path=None):
    """10 维健康度概览：按维度聚合质量门，输出表格。"""
    reg = registry_path or os.path.join(REPO, "runtime", "signal_registry.json")
    if not os.path.exists(reg):
        print(f"[health] 注册表不存在：{reg}")
        return
    d = json.load(open(reg, encoding="utf-8"))
    entries = d.get("entries", [])

    # 按维度聚合
    dim_gates = defaultdict(list)
    for e in entries:
        dim = e.get("dim", "unknown")
        raw = e.get("quality_std") or e.get("quality") or e.get("quality_gate")
        if raw:
            qg = QualityGate.parse(raw)
            dim_gates[dim].append(qg)

    print(f"\n{'维度':<12} {'条数':>4} {'最新质量门':<16} {'分数':>6} {'等级':>4}")
    print("-" * 50)
    for dim in sorted(dim_gates.keys()):
        gates = dim_gates[dim]
        latest = gates[-1]
        meta = DIM_META.get(dim, {"name": dim})
        print(f"{meta.get('name', dim):<10} {len(gates):>4} {str(latest):<16} "
              f"{latest.score():>6.1f} {latest.grade():>4}")
    print()


# ── CLI ──

def main():
    ap = argparse.ArgumentParser(description="质量门标准化模块")
    ap.add_argument("--text", help="解析单条质量门文本")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--backfill", action="store_true", help="回填历史质量门")
    ap.add_argument("--round", type=int, default=None, help="指定轮次回填")
    ap.add_argument("--health", action="store_true", help="10 维健康度概览")
    a = ap.parse_args()

    if a.text:
        qg = QualityGate.from_text(a.text)
        if a.json:
            print(json.dumps(qg.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"原始: {a.text}")
            print(f"标准: {qg}")
            print(f"分数: {qg.score()}")
            print(f"等级: {qg.grade()}")
        return

    if a.backfill:
        backfill_registry(round_num=a.round)
        return

    if a.health:
        health_overview()
        return

    # 无参数 → 自检（解析所有历史格式样例）
    samples = [
        "9✅/5⚠️/0❌",
        "8/8 pass",
        "v2 · 8维 · 0 fail",
        "7/7",
        "8维 0❌",
        "9/9/0",
        "7✅/7⚠️/0❌",
        {"pass": 8, "warn": 1, "fail": 0},
    ]
    print("质量门格式自检（历史格式 → 标准格式）：")
    print("-" * 50)
    for s in samples:
        qg = QualityGate.parse(s)
        src = json.dumps(s, ensure_ascii=False) if isinstance(s, dict) else s
        print(f"  {src:<24} → {str(qg):<16} score={qg.score():<5.1f} grade={qg.grade()}")


if __name__ == "__main__":
    main()
