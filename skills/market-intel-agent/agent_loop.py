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
# L2 Observability：接入 agent_runs_collector（runtime/ 下）
REPO_RUNTIME = os.path.join(HERE, "..", "..", "runtime")
sys.path.insert(0, REPO_RUNTIME)
try:
    from agent_runs_collector import RunCollector
    _HAS_COLLECTOR = True
except Exception:
    _HAS_COLLECTOR = False
from router import route, load_config
from llm_util import call_llm, web_search

# 维度 → agent_id 映射（L2 日志用）
DIM_AGENT_ID = {
    "one": "market-one-wb", "two": "market-two-wb",
    "three": "market-three-wb", "four": "market-four-wb",
    "five": "market-five-wb", "six": "market-six-wb",
    "seven": "market-seven-wb", "mice": "market-seven-wb",
    "potentialsource": "market-potential-wb",
    "broardsignal": "market-broad-wb", "tmc": "market-tmc-wb",
}

DIM_CN = {
    "mice": "会议会展(七)", "one": "休闲度假(一)", "two": "企业协议(二)",
    "three": "会员增购(三)", "four": "餐饮宴会(四)", "five": "长住公寓(五)",
    "six": "数字渠道(六)", "potentialsource": "潜在客源(八)",
    "broardsignal": "潜在广域(九)", "tmc": "TMC订单(十)",
}
GENERATED = os.path.join(HERE, "skills", "_generated")        # 技能自带（独立分发用）
GENERATED_REPO = os.path.join(HERE, "..", "_generated")        # 仓库态（整库 clone 时）
REPO_RUNTIME = os.path.join(HERE, "..", "..", "runtime")      # 仓库态 runtime


def _methodology_dir(dim):
    # 优先用 init.py 重新生成的（酒店专属），其次用技能自带占位（独立分发）
    for base in (GENERATED_REPO, GENERATED):
        p = os.path.join(base, f"market-{dim}-wb", "SKILL.md")
        if os.path.exists(p):
            return p
    return None


def _methodology(dim):
    p = _methodology_dir(dim)
    if not p:
        return "(未找到该维度方法论文件，请先运行 init.py 生成维度技能)"
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
                    # 兼容真实数据字段(canonical_name/spec)与占位字段(name/desc)
                    name = e.get("canonical_name") or e.get("name") or ""
                    desc = e.get("spec") or e.get("desc") or ""
                    status = e.get("status", "")
                    if name or desc:
                        bits.append(f"- {name}：{desc}" + (f" [{status}]" if status else ""))
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


def _mechanism_summary(dim):
    """从 signal_registry 真实信号提炼机制模式摘要（LOOP-015）。

    三层拿来主义第2层深化：从"有数据"到"有机制"。
    提炼信号类型分布 + 客户聚类 + 地理锚点，让 LLM 合成用上机制而非只是信号列表。
    """
    from collections import Counter
    reg = os.path.join(REPO_RUNTIME, "signal_registry.json")
    if not os.path.exists(reg):
        return ""
    try:
        d = json.load(open(reg, encoding="utf-8"))
    except Exception:
        return ""
    entries = [e for e in d.get("entries", []) if e.get("dim") == dim]
    if not entries:
        return ""
    # 信号类型分布（spec 格式：名称|地理|类型）
    types = Counter()
    geos = Counter()
    groups = Counter()
    for e in entries:
        parts = e.get("spec", "").split("|")
        if len(parts) >= 3:
            types[parts[2]] += 1
            if parts[1] and parts[1] != "降级":
                geos[parts[1]] += 1
        if e.get("group"):
            groups[e["group"]] += 1
    top_types = "、".join(f"{t}({c})" for t, c in types.most_common(4))
    top_geos = "、".join(f"{g}({c})" for g, c in geos.most_common(3))
    top_groups = "、".join(f"{g}({c})" for g, c in groups.most_common(5))
    bits = [f"机制模式（基于{len(entries)}条真实信号提炼）："]
    if top_types:
        bits.append(f"- 信号类型分布：{top_types}")
    if top_geos:
        bits.append(f"- 地理锚点：{top_geos}")
    if top_groups:
        bits.append(f"- 头部客户/主体聚类：{top_groups}")
    return "\n".join(bits)


def synthesize(query, dim, hotel, geo):
    """对单个维度产出结构化简报。失败降级为模板。

    三层拿来主义（LOOP-013）：
      第2层 内部沉淀（方法论+signal_registry+harvest）→ 第1层 外部检索（web_search）→ LLM 合成。
    当内部沉淀不足（语料少/标注需外部检索）时，自动调 web_search 补真实外部数据。
    """
    method = _methodology(dim)
    corpus = _corpus(dim)
    mechanism = _mechanism_summary(dim)  # LOOP-015: 机制模式摘要

    # 三层拿来主义第1层：内部沉淀不足时，调外部检索补真实数据
    # 判定不足：语料短于阈值 OR 显式标注需外部检索
    external = ""
    corpus_thin = len(corpus) < 200 or "需外部检索" in corpus or "暂无内部沉淀" in corpus
    if corpus_thin:
        # 用 LLM 生成针对性检索 query（维度+用户查询+地理）
        search_q = f"{geo} {DIM_CN.get(dim, dim)} {query} 最新动态 2026"
        external = web_search(search_q)
        if external:
            external = f"\n## 外部检索（联网真实数据，三层拿来主义第1层）\n{external}\n"

    system = (
        f"你是{hotel}（地理锚点 {geo}）的市场情报分析师，"
        f"负责「{DIM_CN.get(dim, dim)}」方向。依据下方维度方法论与内部沉淀"
        + ("及外部检索真实数据" if external else "")
        + "，回答用户查询，产出可直接用于销售/运营的结构化市场情报简报。\n"
        "严格要求：\n"
        "1. 只基于提供的方法论、内部沉淀"
        + ("及外部检索真实数据" if external else "推理；若内部无数据，必须明确标注「⚠️需外部检索」")
        + "。\n"
        "2. 严禁编造客户名、订单、政策等具体事实。\n"
        "3. 输出分四段：①市场机会 ②可跟进客户/渠道 ③行动建议(本周可做) ④需外部检索项。\n"
        "4. 简洁，中文，总字数<400。"
    )
    user = (
        f"## 维度方法论\n{method}\n\n"
        + (f"## {mechanism}\n\n" if mechanism else "")
        + f"## 内部沉淀（历史信号/资料）\n{corpus}\n"
        f"{external}"
        f"\n## 用户查询\n{query}\n\n请产出简报："
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


def cross_dim_synthesis(query, matched_dims, hotel, geo):
    """跨维度关联推理（LOOP-014）：当≥2维度命中时，产出维度联动机会。

    单维度合成是"点"，跨维度联动是"面"——如央企差旅×会议会展×餐饮宴会三维联动，
    能发现单维度看不到的组合机会，提升元Agent业务价值。
    """
    if len(matched_dims) < 2:
        return ""
    dim_names = "、".join(DIM_CN.get(d, d) for d in matched_dims)
    system = (
        f"你是{hotel}（{geo}）的资深市场情报策略师。用户查询触发了多个业务维度，"
        f"你需要做跨维度关联推理，找出维度之间的联动机会。\n"
        "严格要求：\n"
        "1. 只基于提供的维度名称推理联动逻辑，严禁编造具体客户名/订单/政策。\n"
        "2. 输出分两段：①维度联动机会（这些维度如何组合产生1+1>2的机会）"
        "②联动行动建议（本周可做的跨维度协同动作）。\n"
        "3. 简洁，中文，总字数<300。"
    )
    user = (
        f"## 用户查询\n{query}\n\n"
        f"## 命中的业务维度（{len(matched_dims)}个）\n{dim_names}\n\n"
        "请给出跨维度联动机会分析："
    )
    ans = call_llm(user, system=system, max_tokens=600, temperature=0.3)
    if ans and ans.strip():
        return ans.strip()
    return ""


def answer(query, geo=None, use_llm=False):
    """端到端：路由 + 逐维合成 + 跨维度联动 → 返回完整回复文本（不发送）。"""
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
    matched_keys = []
    for m in rr["matched"]:
        body.append(f"──── {m['name']}（{m['key']}）────")
        # L2 Observability：记录情报产出执行日志（feishu_at/manual 触发路径）
        dim_key = m["key"]
        agent_id = DIM_AGENT_ID.get(dim_key, f"market-{dim_key}-wb")
        if _HAS_COLLECTOR:
            with RunCollector(agent_id, dim_key, trigger="feishu_at") as rc:
                brief = synthesize(query, dim_key, hotel, geo)
                rc.set_output(brief)
        else:
            brief = synthesize(query, dim_key, hotel, geo)
        body.append(brief)
        body.append("")
        matched_keys.append(dim_key)
    if not body:
        body.append("（未匹配到维度，请换一种说法或指定维度）")

    # 跨维度联动推理（LOOP-014）：≥2维度时追加联动机会段
    if len(matched_keys) >= 2:
        cross = cross_dim_synthesis(query, matched_keys, hotel, geo)
        if cross:
            body.append("════ 跨维度联动机会（元Agent关联推理）════")
            body.append(cross)
            body.append("")
    return "\n".join(head + body)


def watch(topic, dim, hotel, geo):
    """事件监控模式：针对监控主题产出「是否新增机会」简报。
    要求 LLM 以【新增】或【无新增】开头，便于 event_watch 做新颖性闸门。

    LOOP-016：主动触发路径与反应式路径（synthesize）能力对齐——
    机制模式提炼 + 三层拿来主义第1层外部检索同样接入，
    保证 event_watch 真推（--send）时的产出质量不弱于 @触发。
    """
    method = _methodology(dim)
    corpus = _corpus(dim)
    mechanism = _mechanism_summary(dim)  # LOOP-016: 机制模式摘要

    # 三层拿来主义第1层：内部沉淀不足时，外部检索补真实数据
    external = ""
    corpus_thin = len(corpus) < 200 or "需外部检索" in corpus or "暂无内部沉淀" in corpus
    if corpus_thin:
        ext = web_search(f"{geo} {DIM_CN.get(dim, dim)} {topic} 最新动态 2026")
        if ext:
            external = f"\n## 外部检索（联网真实数据）\n{ext}\n"

    system = (
        f"你是{hotel}（{geo}）的市场情报监控员，负责「{DIM_CN.get(dim, dim)}」。\n"
        "基于维度方法论与内部沉淀"
        + ("及外部检索真实数据" if external else "")
        + "，判断监控主题是否出现**新增**市场机会。\n"
        "开头必须且仅用【新增】或【无新增】二选一，随后用 ≤200 字给出要点。\n"
        "有外部检索数据时，要点须引用具体事实（名称/日期），严禁编造。\n"
        "无内部数据时，若方法论提示该主题值得持续关注，可用【新增】标注『需外部检索验证』。"
    )
    user = (
        f"## 方法论\n{method}\n\n"
        + (f"## {mechanism}\n\n" if mechanism else "")
        + f"## 内部沉淀\n{corpus}\n"
        f"{external}"
        f"\n## 监控主题\n{topic}\n\n请给出监控结论："
    )
    ans = call_llm(user, system=system, max_tokens=400, temperature=0.2)
    if not ans:
        return f"【无新增】LLM 暂不可用，跳过本轮监控（主题：{topic}）"
    return ans.strip()


def extract_signals(brief, dim, topic):
    """LOOP-018：从【新增】监控简报提取新增信号候选（三层拿来主义第3层自增长）。

    只提取简报中真实提到的具体客户/主体/项目，输出 registry spec 格式
    （名称|地理|类型）。候选写入待审区（harvest_inbox.jsonl），
    不直接写 signal_registry.json——LLM 产出须经人工/独立确认才成真实数据（§3.5分离）。
    """
    system = (
        "你是信号提取助手。从监控简报中提取**新增市场信号**，输出JSON数组。\n"
        "每个信号：{\"name\": \"客户/主体/项目名\", \"geo\": \"地理锚点\", "
        "\"type\": \"client/account/signal/channel 之一\"}\n"
        "严格要求：只提取简报中真实提到的具体客户/主体/项目，严禁编造或泛化。"
        "没有新的具体实体时输出空数组 []。\n只输出JSON数组，不要其他文字。"
    )
    user = (
        f"## 监控主题\n{topic}\n\n## 监控简报\n{brief}\n\n请提取新增信号候选："
    )
    ans = call_llm(user, system=system, max_tokens=300, temperature=0.1)
    if not ans:
        return []
    m = re.search(r'\[.*\]', ans, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    out = []
    known_types = {"client", "account", "signal", "channel"}
    for e in arr:
        if isinstance(e, dict) and str(e.get("name", "")).strip():
            t = str(e.get("type", "signal")).strip()
            out.append({
                "name": str(e["name"]).strip(),
                "geo": str(e.get("geo", "")).strip(),
                "type": t if t in known_types else "signal",  # 归一化到registry规范
            })
    return out


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
