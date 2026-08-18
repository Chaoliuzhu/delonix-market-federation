#!/usr/bin/env python3
"""质量门 v4 机器化 linter（R9 迭代）。

v1/v2 的 6→8 维门全靠主 Agent 逐条肉眼裁决，9 个维度 × 每维 8 条 = 70+ 次判断，
既慢又不一致。v3 把可机检的部分脚本化，主 Agent 只复核 FAIL 与真正需要判断的 WARN。
v3.1 微调容错（来源/外层标记正则扩展）。v4 收紧置信 + 新增增量价值/可执行闭合。

13 维（v3 十一维 + v4 新增 ⑫⑬）：
  ① 来源标注   ② 真实性     ③ 地理相关性  ④ 去重
  ⑤ 可执行性   ⑥ 双层双跑   ⑦ 置信分级(v4收紧)    ⑧ 跨轮一致性
  ⑨ 可执行性量化（SLA/责任人）  ⑩ RICE 完整性  ⑪ 回写闭环对账
  ⑫ 增量价值(v4新增)   ⑬ 可执行闭合(v4新增)

v4 变更摘要：
  - ⑦ 收紧：仅"实时检索快照/自采/推测"三档，"专群沉淀"降为来源类型非置信档；
    且须附依据（URL/快照/出处），裸标签无依据→WARN
  - ⑫ 新增：与 signal_registry.json 历史同客户（canonical_name）比对，
    若 spec 客户已存在且本轮无新事实（仅状态复述）→ WARN "增量价值低"
  - ⑬ 新增：行动须含 ①责任人岗位 ②SLA天数 ③量化目标，三者缺一即 WARN

用法：
  python3 quality_linter.py --round 9                # 扫全部维度
  python3 quality_linter.py --round 9 --dim two      # 单维度
  python3 quality_linter.py --round 9 --json out.json
"""
import os
import re
import sys
import json
import glob
import argparse
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))

CONFIDENCE_LEVELS = ["实时检索快照", "自采", "推测"]  # v4 收紧：仅三档
CONFIDENCE_EVIDENCE_RE = re.compile(  # v4：置信须附依据
    r"(?:依据|来源|出处|快照|URL|http|检索于|采集于)", re.I)
DOWNGRADE_KWS = ["降级", "监测池", "非主场"]
FAR_KWS = ["津南", "国家会展中心", "国展", "50km", "45km", "200km", "远距"]
SLA_RE = re.compile(r"(\d+\s*(天|日|周|个月|小时)|SLA|截止|前完成|之前|月底|"
                    r"\d{1,2}\s*[/\-月]\s*\d{1,2})")
# R13 扩展：补充合法责任主体表述（餐饮部/数字渠道组/外包方联合营销岗 等），
# 消除"行动已指定责任部门/团队却被误判缺责任人"的盲点（同 R12 QUANT_RE 修正逻辑）。
OWNER_RE = re.compile(r"(销售|张惠|李勋|王鑫|夏美娟|陈雷明|赵炳涛|晁留柱|收益|"
                      r"市场部|运营|前厅|人工|主\s*Agent|负责人|责任人|认领|"
                      r"行政|人事|总经理|店长|预订部|宴会部|餐饮部|数字渠道|"
                      r"外包方|岗位|部|组)")
# ① 来源有效性：接受 http(s)、内层/外层沉淀、WebFetch/WebSearch、平台关键词、
# 以及裸域名（tjrc.com.cn / crewcn.com 等），兼容 sub-agent 用「出处」+ 域名写法。
SOURCE_VALID_RE = re.compile(
    r"https?://|[内外]层沉淀|\[内层[^\]]+\]|\[外层[^\]]+\]|WebFetch|WebSearch|招聘|官网|"
    r"政务|采购|定标|工商|公示|平台|招标|msg_id=[a-z0-9_]+|"
    r"[a-z0-9.-]+\.(com|cn|org|net|gov|edu)(\.[a-z]{2})?|"
    # R18 修复：接受 sub-agent 实际使用的真实来源写法——知名媒体/平台名 + 文档类型词，
    # 避免把 美通社/北方网/中国日报/泰达官网/企查查/扬子晚报/搜狐 及 测评/报告/公告/招标公告
    # 等真实可追溯来源误判为「①无有效来源」假 FAIL（同 R11/R12/R13/R16/R17 格式误判修正逻辑）。
    r"专群|消息ID|招标公告|采办公告|采办计划|公告|报道|测评|报告|年报|财报|研报|"
    r"统计局|政府网|日报|晚报|商报|美通社|北方网|中国日报|泰达|搜狐|界面|南方|"
    r"腾讯财经|光明日报|天津日报|津滨海|企查查|中国商网|大众点评|公众号|抖音|小红书", re.I)
# ⑥ 外层标记：除 [外层URL] / http 外，接受「外层（…WebSearch）」「WebFetch」及裸域名
OUTER_RE = re.compile(
    r"\[外层URL\]|https?://|外层（|外层检索|WebSearch|WebFetch|"
    r"[a-z0-9.-]+\.(com|cn|org|net|gov|edu)(\.[a-z]{2})?", re.I)


def first_section(md: str) -> str:
    """截取信号所在章节。

    R8 修复：此前 parse_signals 吃全文，导致第二节「已知/已转交」下的
    `### 2.1 / 2.2 / 2.3` 子标题被误判成信号块（它们没有 来源/置信/RICE 字段，
    必然报 ①无有效来源 → 假 FAIL）。信号只应从信号章节解析。
    R18 修复：sub-agent 模板把「检索过程复盘」放 一、、「信号明细」放 二、，
    故优先定位标题含「信号」的章节；旧模板（一、本轮新信号）仍兼容。
    """
    # 优先：标题含"信号"的章节（R18 起 二、信号明细）
    # R20 修复：原正则 `[^\n]*信号` 在「发现任务信号（逐条含来源…）」处于首个"信号"提前截断，
    # 残留「（逐条含来源 / RICE 推导 / 置信）」被误判为第 4 条信号→假 FAIL(①来源 3/4)。
    # 改为用 finditer 定位首个标题行含"信号"的章节（整行匹配，start 落标题换行之后），消除残留块。
    m_sig = None
    for mm in re.finditer(r"\n#{2,3}\s*[一二三四五六七八九十]、[^\n]*", md):
        if "信号" in mm.group(0):
            m_sig = mm
            break
    if m_sig:
        start = m_sig.end()
        # R18 修复：结束边界须跳过仍含「信号」的续接子章节（如 二、发现任务信号明细），
        # 否则 一、迭代 + 二、发现 同属信号章节时，会在首个 二、 处截断，丢失 D1-D3 等信号块。
        m2 = None
        for mm in re.finditer(r"\n#{2,3}\s*[二三四五六七八九十]、[^\n]*", md[start:]):
            if "信号" not in mm.group(0):
                m2 = mm
                break
        return md[start:start + (m2.start() if m2 else len(md[start:]))]
    # 兼容旧模板：一、本轮新信号
    m = re.search(r"\n#{2,3}\s*一、", md)
    if not m:
        return md
    start = m.end()
    m2 = re.search(r"\n#{2,3}\s*[二三四五六七八九十]、", md[start:])
    return md[start:start + m2.start()] if m2 else md[start:]


def parse_signals(md: str):
    """从 scan_roundN_<dim>.md 解析信号块。宽容匹配各 sub-agent 的格式抖动。"""
    sigs = []
    md = first_section(md)
    # 切块：# 级标题 或 加粗信号头（如 **I1 · name**，R18 sub-agent 格式）
    # 用 lookahead 保留 **ID 在块内，避免 ID 被吃掉导致 head 以 · 开头而误拒
    blocks = re.split(r"\n#{2,4}\s+|\n(?=\*\*[A-Za-z]*\d+[\.、·:：]?\s)", md)
    for b in blocks:
        head = b.split("\n", 1)[0].strip().strip("*").strip()
        is_id_head = bool(re.match(r"^[A-Za-z]*[-]?\d+[\.、·:：]?\s*\S", head))
        # R18：也接受 【标签】ID / 中文ID 头，只要块内含 来源+置信（确为信号块）
        if not is_id_head and not (re.search(r"来源|出处", b) and re.search(r"RICE|置信|rice", b, re.I)):
            continue
        body = b
        def grab(label):
            # label 后允许附加限定词（如「来源URL」「来源 / 出处」），R8 修复：
            # 长住五用 `**来源URL**：`，旧正则要求 label 后紧跟 ** 或冒号，抓不到 → ①来源 0/16 假 FAIL
            m = re.search(rf"[-*]\s*\*{{0,2}}{label}[^\s:：*]{{0,8}}\*{{0,2}}\s*[:：]\s*(.+?)(?=\n\s*[-*]\s*\*{{0,2}}[\u4e00-\u9fffA-Za-z]{{2,8}}\*{{0,2}}\s*[:：]|\n#{{2,}}|\Z)",
                          body, re.S)
            return m.group(1).strip() if m else ""
        sigs.append({
            "title": head,
            "spec": grab("spec").strip("`  "),
            "source": grab("来源") or grab("出处"),
            "confidence": grab("置信"),
            "geo": grab("地理"),
            "finding": grab("发现"),
            "action": grab("行动"),
            "rice": grab("RICE"),
            "raw": body,
        })
    return sigs


def dedup_status(spec: str):
    if not spec:
        return "NOSPEC"
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "dedup2.py"), "check", spec],
            capture_output=True, text=True, timeout=30)
        out = (r.stdout or "").strip()
        return out.split()[0] if out else "ERR"
    except Exception as e:
        return f"ERR:{e}"


def lint_dim(dim: str, round_n: int, do_dedup=True, send_audit=False):
    md_path = os.path.join(HERE, f"scan_round{round_n}_{dim}.md")
    txt_path = os.path.join(HERE, f"signals_r{round_n}_{dim}.txt")
    if not os.path.exists(md_path):
        return None
    md = open(md_path, encoding="utf-8").read()
    sigs = parse_signals(md)
    res = {"dim": dim, "n_signals": len(sigs), "checks": {}, "blockers": [],
           "warns": [], "signals": []}

    def rec(code, status, msg):
        res["checks"][code] = status
        if status == "FAIL":
            res["blockers"].append(f"{code} {msg}")
        elif status == "WARN":
            res["warns"].append(f"{code} {msg}")

    # ⑥ 双层双跑（文件级）
    has_inner = "[内层沉淀]" in md or "内层" in md
    has_outer = bool(OUTER_RE.search(md))
    rec("⑥双层双跑", "PASS" if (has_inner and has_outer) else "FAIL",
        f"内层={has_inner} 外层={has_outer}")

    # ⑧ 跨轮一致性（文件级：须有该章节）
    rec("⑧跨轮一致", "PASS" if re.search(r"跨轮一致性", md) else "WARN",
        "缺『跨轮一致性』章节")

    # ⑭ 声明实发对账（v4 防越权守卫 · 仅 --send-audit 启用）
    # 比对 scan 文件 ⑥/红线「是否发飞书」声明 与 飞书专群实发状态。
    # 实发判定精确匹配：群内 workbuddy 发送的、内容含 R{round} 标记的消息
    # （可区分本 nudge 越权与「宣推日常」等其他自动化合法消息）。
    if send_audit:
        claim = "unknown"
        if re.search(r"已发送", md):
            claim = "sent"
        if re.search(r"未发飞书|未发送飞书|未发送", md):
            claim = "not_sent"
        actual = "unknown"
        note = ""
        cid_m = re.search(r"oc_[a-f0-9]{32}", md)
        if not cid_m:
            note = "未解析到 chat-id，跳过实发读取"
        else:
            cid = cid_m.group(0)
            try:
                import datetime as _dt
                mt = os.path.getmtime(md_path)
                start = _dt.datetime.fromtimestamp(mt, _dt.timezone(
                    _dt.timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")
                rr = subprocess.run(
                    ["lark-cli", "im", "+chat-messages-list", "--as", "bot",
                     "--chat-id", cid, "--order", "asc", "--start", start,
                     "--page-size", "50", "--format", "json"],
                    capture_output=True, text=True, timeout=30)
                dd = json.loads(rr.stdout)
                msgs = dd.get("data", {}).get("messages", [])
                sent = [m for m in msgs
                        if m.get("sender", {}).get("name") == "workbuddy"
                        and f"R{round_n}" in (m.get("content", "") or "")]
                actual = "sent" if sent else "not_sent"
            except Exception as e:
                note = f"lark 读取失败: {e}"
        if claim == "not_sent" and actual == "sent":
            rec("⑭实发对账", "WARN",
                "声明未发飞书但群内已落地 R%d workbuddy 消息（越权）" % round_n)
        elif claim == "sent" and actual == "not_sent":
            rec("⑭实发对账", "WARN",
                "声明已发飞书但群内无 R%d workbuddy 消息（声明不实）" % round_n)
        else:
            rec("⑭实发对账", "PASS", note or "声明与实发一致")
    else:
        rec("⑭实发对账", "PASS", "（实发审计未启用，--send-audit 开启）")

    # ⑪ 回写闭环对账
    n_txt = 0
    if os.path.exists(txt_path):
        n_txt = len([l for l in open(txt_path, encoding="utf-8")
                     if l.strip() and not l.strip().startswith("#")])
    if not os.path.exists(txt_path):
        rec("⑪回写对账", "FAIL", "signals txt 缺失")
    elif n_txt != len(sigs):
        rec("⑪回写对账", "WARN", f"md {len(sigs)} 条 vs txt {n_txt} 条 不一致")
    else:
        rec("⑪回写对账", "PASS", "")

    # 逐条信号
    n_src, n_geo_bad, n_conf, n_act, n_sla, n_rice, n_dup = 0, 0, 0, 0, 0, 0, 0
    # v4 新增：⑫增量价值 + ⑬可执行闭合
    n_increment, n_closure = 0, 0
    # 量化目标正则：含数字+单位（万元/场/人/桌/间/元/单）
    # R17 扩展：补 分(评分)/图(实拍图)/%(占比)/号(门牌/编号)，消除数字渠道维度
    # 用 占比%/评分/图数 作量化目标却被误判缺量化(⑬)的盲点（同 R12 QUANT_RE 修正逻辑）。
    QUANT_RE = re.compile(r"\d+\s*(?:万元?|场|人|桌|间|元|单|份|次|家|批|个|篇|项|条|页|套|户|名|张|位|分|图|%|号)", re.I)
    # 加载历史注册表用于 ⑫ 增量价值检查
    hist_client_map = {}  # canonical_name → [(round, spec)]
    if os.path.exists(os.path.join(HERE, "signal_registry.json")):
        try:
            reg = json.load(open(os.path.join(HERE, "signal_registry.json"),
                                 encoding="utf-8"))
            for e in reg.get("entries", []):
                cn = e.get("canonical_name", "") or e.get("spec", "")
                r = e.get("first_seen_round", 0)
                sp = e.get("spec", "")
                if cn:
                    hist_client_map.setdefault(cn, []).append((r, sp))
        except Exception:
            pass

    for s in sigs:
        sd = {"title": s["title"][:60], "issues": []}
        # ① 来源（R18：source 字段为空时回退扫描 raw 全文，避免 sub-agent 行内来源被判缺失）
        _src = s["source"] or ""
        if SOURCE_VALID_RE.search(_src) or SOURCE_VALID_RE.search(s["raw"]):
            n_src += 1
        else:
            sd["issues"].append("①无有效来源")
        # ③ 地理
        blob = s["geo"] + s["finding"] + s["raw"][:400]
        if any(k in blob for k in FAR_KWS) and not any(k in blob for k in DOWNGRADE_KWS):
            n_geo_bad += 1
            sd["issues"].append("③远距未标降级")
        # ⑦ 置信分级（v4 收紧：仅三档 + 须附依据）
        conf_text = s["confidence"]
        has_conf_level = any(c in conf_text for c in CONFIDENCE_LEVELS)
        has_evidence = bool(CONFIDENCE_EVIDENCE_RE.search(conf_text))
        if has_conf_level and has_evidence:
            n_conf += 1
        elif has_conf_level and not has_evidence:
            sd["issues"].append("⑦置信档正确但无依据")
        else:
            sd["issues"].append("⑦置信未分级或用非标档")
        # ⑤/⑨ 可执行 + 量化
        if s["action"]:
            n_act += 1
            has_sla = bool(SLA_RE.search(s["action"]))
            has_owner = bool(OWNER_RE.search(s["action"]))
            if has_sla and has_owner:
                n_sla += 1
            else:
                sd["issues"].append("⑨行动缺 SLA 或责任人")
        else:
            sd["issues"].append("⑤无行动闭环")
        # ⑩ RICE —— R8 修复：兼容 `R=4 I=4 C=4 E=3` 与 `R4×I4×C4×E3 = 192` 两种写法
        m = re.findall(r"[RICE]\s*=?\s*(\d+)", s["rice"])
        if len(m) >= 4:
            n_rice += 1
            nums = [int(x) for x in m[:4]]
            mt = (re.search(r"总分\s*\**\s*(\d+)", s["rice"])
                  or re.search(r"=\s*\**\s*(\d+)\**\s*$", s["rice"].strip()))
            if mt:
                sd["rice_total"] = int(mt.group(1))
            elif "×" in s["rice"] or "*" in s["rice"]:
                sd["rice_total"] = nums[0] * nums[1] * nums[2] * nums[3]
            else:
                sd["rice_total"] = sum(nums)
        else:
            sd["issues"].append("⑩RICE 不完整")
        # ④ 去重
        if do_dedup:
            st = dedup_status(s["spec"])
            sd["dedup"] = st
            if st.startswith("SEEN") or st.startswith("FUZZY"):
                n_dup += 1
                sd["issues"].append(f"④疑似重复({st})")
        # ⑫ 增量价值（v4 新增）：与历史同客户比对，纯重提降权
        # 提取 spec 的客户名称（canonical_name 逻辑简化版）
        cn_name = re.split(r"[|｜]", s.get("spec", ""))[0].strip()
        hist = hist_client_map.get(cn_name, [])
        if hist:
            # 同客户历史已有，需判断本轮是否有新事实（粗判：spec 文本不完全一致）
            old_specs = [sp for (r, sp) in hist if r != round_n]
            if s.get("spec", "").strip() in old_specs:
                sd["issues"].append("⑫增量价值低（同客户同 spec 重提）")
            else:
                n_increment += 1  # 同客户但新 spec = 有增量
        else:
            n_increment += 1  # 新客户 = 有增量
        # ⑬ 可执行闭合（v4 新增）：行动须含 ①责任人岗位 ②SLA天数 ③量化目标
        if s["action"]:
            has_quant = bool(QUANT_RE.search(s["action"]))
            if has_sla and has_owner and has_quant:
                n_closure += 1
            else:
                missing = []
                if not has_owner:
                    missing.append("责任人岗位")
                if not has_sla:
                    missing.append("SLA天数")
                if not has_quant:
                    missing.append("量化目标")
                sd["issues"].append(f"⑬可执行闭合缺：{'+'.join(missing)}")
        res["signals"].append(sd)

    n = max(len(sigs), 1)
    rec("①来源标注", "PASS" if n_src == len(sigs) else
        ("WARN" if n_src >= n * 0.8 else "FAIL"), f"{n_src}/{len(sigs)}")
    rec("③地理相关", "PASS" if n_geo_bad == 0 else "WARN", f"{n_geo_bad} 条远距未降级")
    rec("⑦置信分级", "PASS" if n_conf == len(sigs) else "WARN",
        f"{n_conf}/{len(sigs)} 三档+依据")
    rec("⑤可执行性", "PASS" if n_act == len(sigs) else "WARN", f"{n_act}/{len(sigs)}")
    rec("⑨执行量化", "PASS" if n_sla == len(sigs) else "WARN",
        f"{n_sla}/{len(sigs)} 含 SLA+责任人")
    rec("⑩RICE完整", "PASS" if n_rice == len(sigs) else "WARN", f"{n_rice}/{len(sigs)}")
    rec("⑫增量价值", "PASS" if n_increment == len(sigs) else "WARN",
        f"{n_increment}/{len(sigs)} 有增量（同客户新spec或新客户）")
    rec("⑬可执行闭合", "PASS" if n_closure == len(sigs) else "WARN",
        f"{n_closure}/{len(sigs)} 含岗位+SLA+量化")
    if do_dedup:
        rec("④去重", "PASS" if n_dup == 0 else "WARN", f"{n_dup} 条疑似重复")
    # ② 真实性（启发式：有「待核验/未闭环/缺口」= 诚实；全无 = 提示）
    honest = bool(re.search(r"待核验|未闭环|数据缺口|需人工|OPEN", md))
    rec("②真实性", "PASS" if honest else "WARN",
        "未见任何待核验/缺口声明，需人工确认是否过度自信")
    return res


STATUS_ORDER = {"FAIL": 0, "WARN": 1, "PASS": 2}
DIMS = ["mice", "one", "two", "three", "four", "five", "six",
        "potentialsource", "broardsignal", "tmc"]
DIM_CN = {"mice": "MICE(七)", "one": "休闲(一)", "two": "企业(二)",
          "three": "会员(三)", "four": "餐饮宴会(四)", "five": "长住(五)",
          "six": "数字(六)", "potentialsource": "潜在客源(八)",
          "broardsignal": "潜在广域(九)", "tmc": "TMC(十)"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=4)
    ap.add_argument("--dim", default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--no-dedup", action="store_true")
    ap.add_argument("--send-audit", action="store_true",
                    help="启用⑭声明实发对账守卫（读取飞书专群实发状态比对scan文件声明）")
    a = ap.parse_args()

    dims = [a.dim] if a.dim else DIMS
    results = []
    for d in dims:
        r = lint_dim(d, a.round, not a.no_dedup, send_audit=a.send_audit)
        if r:
            results.append(r)

    print(f"# 质量门 v4 · R{a.round} 机器裁决\n")
    print("| 维度 | 信号 | PASS | WARN | FAIL | 判定 |")
    print("|---|---|---|---|---|---|")
    tot_p = tot_w = tot_f = 0
    for r in results:
        p = sum(1 for v in r["checks"].values() if v == "PASS")
        w = sum(1 for v in r["checks"].values() if v == "WARN")
        f = sum(1 for v in r["checks"].values() if v == "FAIL")
        tot_p += p; tot_w += w; tot_f += f
        verdict = "❌ BLOCKED" if f else ("⚠️ 降级发送" if w else "✅ SEND-ELIGIBLE")
        print(f"| {DIM_CN.get(r['dim'], r['dim'])} | {r['n_signals']} | "
              f"{p} | {w} | {f} | {verdict} |")
    print(f"| **合计** | **{sum(r['n_signals'] for r in results)}** | "
          f"**{tot_p}** | **{tot_w}** | **{tot_f}** | |")

    print("\n## FAIL（必须主 Agent 复核）")
    any_f = False
    for r in results:
        for b in r["blockers"]:
            print(f"- [{DIM_CN.get(r['dim'], r['dim'])}] {b}")
            any_f = True
    if not any_f:
        print("- （无）")

    print("\n## WARN 明细")
    for r in results:
        if r["warns"]:
            print(f"\n**{DIM_CN.get(r['dim'], r['dim'])}**")
            for w in r["warns"]:
                print(f"  - {w}")

    print("\n## 逐条信号问题（前 5/维）")
    for r in results:
        bad = [s for s in r["signals"] if s["issues"]]
        if bad:
            print(f"\n**{DIM_CN.get(r['dim'], r['dim'])}** ({len(bad)}/{r['n_signals']} 条有问题)")
            for s in bad[:5]:
                print(f"  - {s['title']}: {', '.join(s['issues'])}")

    # RICE 排行
    print("\n## RICE TOP 15（跨维度高价值信号）")
    allsig = []
    for r in results:
        for s in r["signals"]:
            if s.get("rice_total"):
                allsig.append((s["rice_total"], DIM_CN.get(r["dim"], r["dim"]),
                               s["title"]))
    for sc, dm, t in sorted(allsig, reverse=True)[:15]:
        print(f"  {sc:3d}  [{dm}] {t}")

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\nJSON → {a.json}")


if __name__ == "__main__":
    main()
