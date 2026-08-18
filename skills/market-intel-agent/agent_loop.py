#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_loop.py · 市场情报 Agent 执行闭环（"完全 Agent 效果"核心）

把「路由」升级为「真正产出情报」：
  触发（飞书@ / 事件监控 / 直接调用）→ 路由维度 → 对每个维度：
    1. 拉取该维度方法论（skills/_generated/market-<dim>-wb/SKILL.md）
    2. 拉取内部沉淀（signal_registry 同维条目 + runtime/harvest/<dim>/ 资料）
    3. 调 LLM 综合产出「结构化市场情报简报」（机会/客户/行动/需外部检索）
  合并为一条回复，可选推送飞书群。

诚实边界：
  - 内部语料驱动的合成 + LLM 推理 是真实闭环（用上我们的历史沉淀与去重注册表）。
  - 真实「外部 Web 检索」由主 Agent（WorkBuddy 侧，自带 WebSearch）执行，
    或运营者在 WorkBuddy 调维度 skill 完成；本脚本产出里会明确标注「需外部检索」项，
    不伪造外部数据。外部检索后端可在 llm_util 之上扩展（见 README）。
  - LLM 不可用时优雅降级为「方法论摘要 + 待办」模板，绝不崩溃。

用法：
  python3 agent_loop.py "最近天津央企有什么培训住宿机会" --use-llm
  python3 agent_loop.py "竞品在搞什么促销" --use-llm --json
  （飞书触发 / 事件监控由 feishu_receiver.py / event_watch.py 调用本模块）
"""
import json
import os
import re
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from router import route, load_config
from llm_util import call_llm

DIM_CN = {
    "mice": "会议会展(七)", "one": "休闲度假(一)", "two": "企业协议(二)",
    "three": "会员增购(三)", "four": "餐饮宴会(四)", "five": "长住公寓(五)",
    "six": "数字渠道(六)", "potentialsource": "潜在客源(八)",
    "broardsignal": "潜在广域(九)", "tmc": "TMC订单(十)",
}
GENERATED = os.path.join(HERE, "..", "_generated")            # skills/_generated
REPO_RUNTIME = os.path.join(HERE, "..", "..", "runtime")      # 仓库态 runtime


def _methodology(dim):
    p = os.path.join(GENERATED, f"market-{dim}-wb", "SKILL.md")
    if not os.path.exists(p):
        return "(未找到该维度方法论文件，请先运行 init.py 生成 skills/_generated)"
    try:
        txt = open(p, encoding="utf-8").read()
    except Exception:
        return "(方法论读取失败)"
    return txt[:4500]  # 截断，避免 prompt 过长


def _corpus(dim):
    bits = []
    reg = os.path.join(REPO_RUNTIME, "signal_registry.json")
    if os.path.exists(reg):
        try:
            d = json.load(open(reg, encoding="utf-8"))
            for e in d.get("entries", []):
                if e.get("dim") == dim:
                    bits.append(f"- {e.get('name','')}：{e.get('desc','')}")
        except Exception:
            pass
    hdir = os.path.join(REPO_RUNTIME, "harvest", dim)
    if os.path.isdir(hdir):
        for f in sorted(os.listdir(hdir))[:5]:
            if f.endswith((".txt", ".md", ".json")):
                try:
                    t = open(os.path.join(hdir, f), encoding="utf-8").read()[:1200]
                    bits.append(f"# 资料 {f}\n{t}")
                except Exception:
                    pass
    return "\n".join(bits) if bits else "(暂无内部沉淀数据，需外部检索补全)"


def synthesize(query, dim, hotel, geo):
    """对单个维度产出结构化简报。失败降级为模板。"""
    method = _methodology(dim)
    corpus = _corpus(dim)
    system = (
        f"你是{hotel}（地理锚点 {geo}）的市场情报分析师，"
        f"负责「{DIM_CN.get(dim, dim)}」方向。依据下方维度方法论与内部沉淀，"
        "回答用户查询，产出可直接用于销售/运营的结构化市场情报简报。\n"
        "严格要求：\n"
        "1. 只基于提供的方法论与内部沉淀推理；若内部无数据，必须明确标注「⚠️需外部检索」。\n"
        "2. 严禁编造客户名、订单、政策等具体事实。\n"
        "3. 输出分四段：①市场机会 ②可跟进客户/渠道 ③行动建议(本周可做) ④需外部检索项。\n"
        "4. 简洁，中文，总字数<400。"
    )
    user = (
        f"## 维度方法论\n{method}\n\n"
        f"## 内部沉淀（历史信号/资料）\n{corpus}\n\n"
        f"## 用户查询\n{query}\n\n请产出简报："
    )
    ans = call_llm(user, system=system, max_tokens=900, temperature=0.3)
    if ans and ans.strip():
        return ans.strip()
    # 降级：模板
    return (
        f"⚠️ LLM 暂不可用，以下为方法论要点提示（需人工/外部检索补全）：\n"
        f"- 维度方法论片段：{method[:200]}…\n"
        f"- 内部沉淀：{corpus[:150]}\n"
        f"- 建议：在 WorkBuddy 调本维度 skill 执行「双层拿来主义」获取实时情报。"
    )


def answer(query, geo=None, use_llm=False):
    """端到端：路由 + 逐维合成 → 返回完整回复文本（不发送）。"""
    cfg = load_config()
    hotel = cfg.get("hotel", "本酒店")
    geo = geo or cfg.get("geo", "未指定")
    rr = route(query, geo=geo, use_llm=use_llm)
    head = [
        f"🤖 {hotel}·市场情报 Agent",
        f"查询：{query}",
        f"路由：{rr['mode']}（{'LLM语义' if rr.get('llm') else '关键词'}）｜理由：{rr.get('reason','')}",
        "",
    ]
    body = []
    for m in rr["matched"]:
        body.append(f"──── {m['name']}（{m['key']}）────")
        body.append(synthesize(query, m["key"], hotel, geo))
        body.append("")
    if not body:
        body.append("（未匹配到维度，请换一种说法或指定维度）")
    return "\n".join(head + body)


def watch(topic, dim, hotel, geo):
    """事件监控模式：针对监控主题产出「是否新增机会」简报。
    要求 LLM 以【新增】或【无新增】开头，便于 event_watch 做新颖性闸门。"""
    method = _methodology(dim)
    corpus = _corpus(dim)
    system = (
        f"你是{hotel}（{geo}）的市场情报监控员，负责「{DIM_CN.get(dim, dim)}」。\n"
        "基于维度方法论与内部沉淀，判断监控主题是否出现**新增**市场机会。\n"
        "开头必须且仅用【新增】或【无新增】二选一，随后用 ≤200 字给出要点。\n"
        "无内部数据时，若方法论提示该主题值得持续关注，可用【新增】标注『需外部检索验证』。"
    )
    user = (
        f"## 方法论\n{method}\n\n## 内部沉淀\n{corpus}\n\n"
        f"## 监控主题\n{topic}\n\n请给出监控结论："
    )
    ans = call_llm(user, system=system, max_tokens=400, temperature=0.2)
    if not ans:
        return f"【无新增】LLM 暂不可用，跳过本轮监控（主题：{topic}）"
    return ans.strip()


def post(chat_id, text, lark_bin="lark-cli", send=False, idem=None):
    """推送至飞书群。默认 dry-run 只打印。"""
    if not send:
        print("    [DRY-RUN 不发送] 拟推送：")
        for ln in text.split("\n"):
            print("      " + ln)
        return
    cmd = [lark_bin, "im", "+messages-send", "--as", "bot",
           "--chat-id", chat_id, "--text", text]
    if idem:
        cmd += ["--idempotency-key", idem]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        ok = (r.returncode == 0) and ("ok\": true" in r.stdout or "ok\":true" in r.stdout)
        print("    推送：" + ("OK" if ok else "FAIL " + r.stdout[:120]))
    except Exception as e:
        print(f"    推送异常：{e}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--geo", default=None)
    ap.add_argument("--use-llm", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out = answer(a.query, geo=a.geo, use_llm=a.use_llm)
    if a.json:
        print(json.dumps({"reply": out}, ensure_ascii=False, indent=2))
    else:
        print(out)
