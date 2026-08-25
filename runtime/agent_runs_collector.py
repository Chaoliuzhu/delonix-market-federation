#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_runs_collector.py · L2 Observability 执行日志收集器

每次 Agent 执行产生一条 JSONL 记录，写入 agent_runs.jsonl。
通过上下文管理器接入 agent_loop.py / event_watch.py：

    from agent_runs_collector import RunCollector

    with RunCollector(agent_id="market-one-wb", dim="one", trigger="event_watch") as rc:
        result = synthesize(query, dim, hotel, geo)
        rc.set_output(result)
        rc.set_tokens(token_count)   # 可选

异常时自动记录 error status；正常退出记录 success。

日志字段（L2 Schema，见设计文档 §3.1）：
  run_id / timestamp / agent_id / dim / trigger / duration_ms /
  tokens / status / output_digest / error_msg

健康度评分（见设计文档 §3.3）：
  score = 0.4×成功率 + 0.3×产出量 + 0.2×时延 + 0.1×活跃度
  等级：80-100 优秀🟢 / 60-79 正常🟡 / 40-59 退化🟠 / 0-39 严重🔴

用法：
  # 代码接入（上下文管理器）
  with RunCollector("market-one-wb", "one", "event_watch") as rc:
      ...

  # CLI：健康度查询
  python3 agent_runs_collector.py --health              # 全量健康度
  python3 agent_runs_collector.py --health --dim one    # 单维度
  python3 agent_runs_collector.py --tail 10             # 最近10条日志
"""
import argparse
import fcntl
import json
import os
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LOG_PATH = os.path.join(REPO, "runtime", "agent_runs.jsonl")

# 东八区时区
CST = timezone(timedelta(hours=8))

# 维度 → agent_id 映射（与 quality_gate.py / capabilities.json 对齐）
DIM_AGENT = {
    "one": "market-one-wb",
    "two": "market-two-wb",
    "three": "market-three-wb",
    "four": "market-four-wb",
    "five": "market-five-wb",
    "six": "market-six-wb",
    "seven": "market-seven-wb",
    "mice": "market-seven-wb",
    "potentialsource": "market-potential-wb",
    "broardsignal": "market-broad-wb",
    "tmc": "market-tmc-wb",
}
AGENT_DIM = {v: k for k, v in DIM_AGENT.items()}

# 触发方式枚举
TRIGGERS = ("feishu_at", "event_watch", "manual")
# 状态枚举
STATUSES = ("success", "error", "timeout", "degraded")


class RunCollector:
    """上下文管理器：自动记录 Agent 执行日志。

    用法：
        with RunCollector(agent_id="market-one-wb", dim="one",
                          trigger="event_watch") as rc:
            result = do_work()
            rc.set_output(result)        # 设置产出摘要
            rc.set_tokens(1500)          # 可选：设置 token 消耗
            rc.set_status("success")     # 可选：显式状态（默认 success）

    异常时自动捕获并记录 error status，然后 re-raise（不吞异常）。
    """

    def __init__(self, agent_id, dim, trigger="manual",
                 log_path=None, extra=None):
        self.agent_id = agent_id
        self.dim = dim
        self.trigger = trigger if trigger in TRIGGERS else "manual"
        self.log_path = log_path or LOG_PATH
        self.extra = extra or {}

        self.run_id = f"{agent_id}_{uuid.uuid4().hex[:8]}"
        self.start_ts = None
        self.end_ts = None
        self.duration_ms = 0
        self.tokens = 0
        self.status = "success"
        self.output_digest = ""
        self.error_msg = ""
        self._entered = False

    def __enter__(self):
        self.start_ts = time.time()
        self._entered = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_ts = time.time()
        self.duration_ms = int((self.end_ts - self.start_ts) * 1000)
        if exc_type is not None:
            self.status = "error"
            self.error_msg = f"{exc_type.__name__}: {exc_val}"[:500]
        elif self.status == "success" and not self.output_digest:
            # 无产出也标记 success（可能是 dry-run），但产出为空
            pass
        self._write_log()
        # 不吞异常：返回 None（falsy）让异常继续传播
        return None

    def set_output(self, text):
        """设置产出摘要（前 200 字符）。"""
        if text:
            self.output_digest = str(text)[:200].replace("\n", " ")

    def set_tokens(self, n):
        """设置 LLM token 消耗。"""
        self.tokens = int(n or 0)

    def set_status(self, status):
        """显式设置状态（success/error/timeout/degraded）。"""
        if status in STATUSES:
            self.status = status

    def _write_log(self):
        """追加一条 JSONL 日志（文件锁防并发写冲突）。"""
        rec = {
            "run_id": self.run_id,
            "timestamp": datetime.now(CST).isoformat(timespec="seconds"),
            "agent_id": self.agent_id,
            "dim": self.dim,
            "trigger": self.trigger,
            "duration_ms": self.duration_ms,
            "tokens": self.tokens,
            "status": self.status,
            "output_digest": self.output_digest,
            "error_msg": self.error_msg,
        }
        rec.update(self.extra)
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except Exception as e:
            # 日志写入失败不应阻断主流程
            sys.stderr.write(f"[RunCollector] 日志写入失败：{e}\n")


# ── 健康度评分（设计文档 §3.3）──

def load_runs(log_path=None, dim=None, agent_id=None, hours=168):
    """加载日志记录。

    Args:
        dim: 过滤维度
        agent_id: 过滤 agent_id
        hours: 只看最近 N 小时（默认 168h = 7 天）
    Returns:
        list[dict] 日志记录
    """
    path = log_path or LOG_PATH
    if not os.path.exists(path):
        return []
    cutoff = time.time() - hours * 3600
    runs = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if dim and r.get("dim") != dim:
            continue
        if agent_id and r.get("agent_id") != agent_id:
            continue
        # 时间过滤（用 timestamp 字段，解析失败则保留）
        ts = r.get("timestamp", "")
        try:
            t = datetime.fromisoformat(ts).timestamp()
            if t < cutoff:
                continue
        except Exception:
            pass
        runs.append(r)
    return runs


def compute_health_score(runs):
    """计算健康度评分（0-100）。

    score = 0.4×成功率分 + 0.3×产出量分 + 0.2×时延分 + 0.1×活跃度分
    """
    if not runs:
        return 0, "🔴", "严重", {"total": 0}
    total = len(runs)
    success = sum(1 for r in runs if r.get("status") == "success")
    has_output = sum(1 for r in runs if r.get("output_digest"))
    durations = [r.get("duration_ms", 0) for r in runs if r.get("duration_ms")]

    # 成功率分
    success_rate = (success / total * 100) if total else 0
    # 产出量分
    output_rate = (has_output / total * 100) if total else 0
    # 时延分：<5s=100, 5-30s=80, 30-60s=60, >60s=40
    if durations:
        avg_ms = sum(durations) / len(durations)
        if avg_ms < 5000:
            latency_score = 100
        elif avg_ms < 30000:
            latency_score = 80
        elif avg_ms < 60000:
            latency_score = 60
        else:
            latency_score = 40
    else:
        latency_score = 0
    # 活跃度分：≥10次=100, 5-9次=80, 1-4次=60, 0次=0
    if total >= 10:
        activity_score = 100
    elif total >= 5:
        activity_score = 80
    elif total >= 1:
        activity_score = 60
    else:
        activity_score = 0

    score = (0.4 * success_rate + 0.3 * output_rate +
             0.2 * latency_score + 0.1 * activity_score)
    score = round(score, 1)

    # 等级
    if score >= 80:
        icon, grade = "🟢", "优秀"
    elif score >= 60:
        icon, grade = "🟡", "正常"
    elif score >= 40:
        icon, grade = "🟠", "退化"
    else:
        icon, grade = "🔴", "严重"

    detail = {
        "total": total, "success": success, "has_output": has_output,
        "success_rate": round(success_rate, 1),
        "output_rate": round(output_rate, 1),
        "avg_duration_ms": int(sum(durations) / len(durations)) if durations else 0,
        "latency_score": latency_score, "activity_score": activity_score,
    }
    return score, icon, grade, detail


def health_overview(dim=None, hours=168, log_path=None):
    """10 维健康度概览。"""
    all_runs = load_runs(log_path=log_path, hours=hours)
    if not all_runs:
        print("[health] 暂无执行日志（agent_runs.jsonl 不存在或为空）")
        print("         Agent 执行后自动产生日志，届时再查询。")
        return

    # 按维度分组
    dim_runs = defaultdict(list)
    for r in all_runs:
        dim_runs[r.get("dim", "unknown")].append(r)

    print(f"\n{'维度':<12} {'执行数':>5} {'成功率':>6} {'均时延':>8} "
          f"{'健康分':>6} {'等级':<8}")
    print("-" * 55)
    for d in sorted(dim_runs.keys()):
        runs = dim_runs[d]
        score, icon, grade, det = compute_health_score(runs)
        dim_name = {
            "one": "休闲度假", "two": "企业协议", "three": "会员增购",
            "four": "餐饮宴会", "five": "长住公寓", "six": "数字渠道",
            "seven": "会议会展", "mice": "会议会展",
            "potentialsource": "潜在客源", "broardsignal": "潜在广域",
            "tmc": "TMC订单",
        }.get(d, d)
        print(f"{dim_name:<10} {det['total']:>5} {det['success_rate']:>5.0f}% "
              f"{det['avg_duration_ms']:>7}ms {score:>6.1f} {icon}{grade}")

    # 整体
    score, icon, grade, det = compute_health_score(all_runs)
    print("-" * 55)
    print(f"{'整体':<10} {det['total']:>5} {det['success_rate']:>5.0f}% "
          f"{det['avg_duration_ms']:>7}ms {score:>6.1f} {icon}{grade}\n")


def tail_runs(n=10, log_path=None):
    """显示最近 N 条日志。"""
    path = log_path or LOG_PATH
    if not os.path.exists(path):
        print(f"[tail] 日志不存在：{path}")
        return
    lines = open(path, encoding="utf-8").readlines()
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            print(f"[{r.get('timestamp','')}] {r.get('agent_id',''):<20} "
                  f"{r.get('dim',''):<8} {r.get('status',''):<8} "
                  f"{r.get('duration_ms',0)}ms")
        except Exception:
            print(line)


def main():
    ap = argparse.ArgumentParser(description="L2 Observability 执行日志收集器")
    ap.add_argument("--health", action="store_true", help="健康度概览")
    ap.add_argument("--dim", default=None, help="过滤维度")
    ap.add_argument("--hours", type=int, default=168, help="时间窗口（小时）")
    ap.add_argument("--tail", type=int, default=0, help="显示最近 N 条日志")
    ap.add_argument("--test", action="store_true", help="写入测试日志后退出")
    a = ap.parse_args()

    if a.tail:
        tail_runs(a.tail)
        return

    if a.test:
        # 写一条测试日志验证管道
        with RunCollector("market-one-wb", "one", "manual") as rc:
            rc.set_output("测试执行：休闲度假维度巡检正常")
            rc.set_tokens(800)
        print("[test] 测试日志已写入 → runtime/agent_runs.jsonl")
        return

    if a.health:
        health_overview(dim=a.dim, hours=a.hours)
        return

    # 无参数 → 自检
    print("RunCollector 自检：写入测试日志 → 查询健康度")
    print("-" * 50)
    with RunCollector("market-one-wb", "one", "manual") as rc:
        rc.set_output("自检：休闲度假维度模拟执行")
        rc.set_tokens(500)
    with RunCollector("market-two-wb", "two", "event_watch") as rc:
        rc.set_output("自检：企业协议维度模拟执行")
        rc.set_tokens(600)
    print("✅ 2 条测试日志已写入")
    print("\n健康度概览：")
    health_overview()


if __name__ == "__main__":
    main()
