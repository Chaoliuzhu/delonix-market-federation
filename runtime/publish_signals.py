#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
发布一轮迭代信号到飞书专群 + Bitable 自建 base。
- 飞书：每维度一张 post 卡片（结构化，避免 \\n），幂等 key round<N>-<dim>-<date>
- Bitable：每信号一条 record（bot 自建 base，有写权）
- 输出 push_r<N>_results.json（含 om_ 幂等回执）

用法：
  python3 publish_signals.py --round 4 --date 20260804
  python3 publish_signals.py --round 5 --date 20260805
  python3 publish_signals.py --round 6 --date 20260806
"""
import argparse, json, glob, os, re, subprocess, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
import runtime_config as rc
CFG = rc.CFG
LARK = CFG["lark_bin"]
BASE_TOKEN = CFG["base_token"]
TABLE_ID = CFG["table_id"]
DIM_CHAT = CFG["dim_chat"]
DIM_CN = CFG["dim_cn"]
HOTEL = CFG["hotel"]

def parse_signals(path):
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip():
            continue
        # 跳过表头行（spec TAB 来源 TAB 置信...）
        if line.lower().startswith("spec\t"):
            continue
        # 跳过 txt 注释/表头行，避免把「# 格式/# 来源/# 红线」等自检信息发到飞书 (R23 发现)
        if line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        head = parts[0]
        rice = 0
        desc = ""
        
        # 策略1：标准3列格式 spec|类型 TAB RICE(纯数字) TAB 描述
        if len(parts) >= 3:
            # 第2列是纯数字 = RICE
            try:
                rice = int(float(parts[1]))
                desc = parts[2] if len(parts) > 2 else ""
            except ValueError:
                rice = 0
                # 策略2：多列格式（如7列 spec TAB 来源 TAB 置信 TAB 地理 TAB 发现 TAB 行动 TAB RICE）
                # 找最后一列中的 RICE 表达式
                for p in reversed(parts[1:]):
                    # 先试 R4×I4×C4×E4=400 或 R=4 I=4 C=4 E=4=256
                    m = re.search(r"R\s*=?\s*(\d+)\s*[×x*]?\s*I\s*=?\s*(\d+)\s*[×x*]?\s*C\s*=?\s*(\d+)\s*[×x*]?\s*E\s*=?\s*(\d+)", p)
                    if m:
                        nums = [int(x) for x in m.groups()]
                        mt = re.search(r"=\s*(\d+)", p)
                        if mt:
                            rice = int(mt.group(1))
                        elif any(op in p for op in ["×", "*", "x"]):
                            rice = nums[0] * nums[1] * nums[2] * nums[3]
                        else:
                            rice = sum(nums)
                        break
                    # 再试 RICE=数字
                    m2 = re.search(r"RICE\s*=?\s*(\d+)", p, re.I)
                    if m2:
                        rice = int(m2.group(1))
                        break
                # desc = 合并所有非RICE列
                if not desc:
                    desc_parts = [p for p in parts[1:] if not re.match(r"R\s*=?\s*\d+.*[ICE]\s*=?\s*\d+", p) and not p.isdigit()]
                    desc = " ".join(desc_parts[:3]) if desc_parts else parts[-1]
        elif len(parts) == 2:
            # 策略3：2列格式 spec TAB RICE或描述
            try:
                rice = int(float(parts[1]))
            except ValueError:
                # pipe-only，从整行提取
                m = re.search(r"R\s*=?\s*(\d+)\s*[×x*]?\s*I\s*=?\s*(\d+)\s*[×x*]?\s*C\s*=?\s*(\d+)\s*[×x*]?\s*E\s*=?\s*(\d+)", line)
                if m:
                    nums = [int(x) for x in m.groups()]
                    mt = re.search(r"=\s*(\d+)", line)
                    rice = int(mt.group(1)) if mt else (nums[0]*nums[1]*nums[2]*nums[3] if "×" in line or "*" in line else sum(nums))
                else:
                    m2 = re.search(r"RICE\s*=?\s*(\d+)", line, re.I)
                    rice = int(m2.group(1)) if m2 else 0
                desc = parts[1] if len(parts[1]) > 10 else ""
        else:
            # 策略4：1列（pipe-only），从整行提取
            m = re.search(r"R\s*=?\s*(\d+)\s*[×x*]?\s*I\s*=?\s*(\d+)\s*[×x*]?\s*C\s*=?\s*(\d+)\s*[×x*]?\s*E\s*=?\s*(\d+)", line)
            if m:
                nums = [int(x) for x in m.groups()]
                mt = re.search(r"=\s*(\d+)", line)
                rice = int(mt.group(1)) if mt else (nums[0]*nums[1]*nums[2]*nums[3] if "×" in line or "*" in line else sum(nums))
            else:
                m2 = re.search(r"RICE\s*=?\s*(\d+)", line, re.I)
                rice = int(m2.group(1)) if m2 else 0
            desc = line[:200]
        
        # desc 兜底：如果还是空，用整行
        if not desc:
            desc = line[:200]
        
        # 区分「迭代核验」与「新发现」，用于飞书消息分组展示（解决重复观感）
        phase = "iter" if "迭代核验" in desc else "new"

        name = head.split("|")[0].strip()
        typ = head.split("|")[-1].strip() if "|" in head else ""
        out.append({"name": name, "type": typ, "rice": rice, "desc": desc, "phase": phase})
    return out

def load_verdicts(round_n):
    p = os.path.join(HERE, f"r{round_n}_gate.json")
    if not os.path.exists(p):
        return {}
    d = json.load(open(p))
    m = {}
    for e in d:
        dim = e["dim"]
        verdict = e.get("verdict", "")
        # 统计 P/W/S
        tp = tw = tf = 0
        for gate, st in e.get("checks", {}).items():
            s = str(st)
            if s == "PASS": tp += 1
            elif s == "WARN": tw += 1
            elif s == "FAIL": tf += 1
        verdict = "BLOCKED" if tf else ("降级发送" if tw else "SEND-ELIGIBLE")
        m[dim] = {"verdict": verdict, "p": tp, "w": tw, "f": tf}
    return m

def build_post(dim, sigs, round_n, verdict_info):
    vi = verdict_info or {"verdict": "待裁决", "p": 0, "w": 0, "f": 0}
    title = f"R{round_n} 信息机会 · {DIM_CN.get(dim, dim)}"
    content = []
    news = [s for s in sigs if s.get("phase") == "new"]
    iters = [s for s in sigs if s.get("phase") == "iter"]
    head = (f"📡 本轮 {len(sigs)} 条信号（✨新发现 {len(news)} ／ 🔍迭代核验 {len(iters)}）"
            f" ｜ 质量门 {vi['p']}✅/{vi['w']}⚠️/{vi['f']}❌"
            f" ｜ 判定：{vi['verdict']} ｜ 周期 R{round_n}")
    content.append([{"tag": "text", "text": head}])
    # ✨ 新发现分组
    if news:
        content.append([{"tag": "text", "text": f"✨ 新发现 {len(news)} 条"}])
        for i, s in enumerate(news, 1):
            content.append([{"tag": "text",
                             "text": f"{i}. {s['name']} 〔{s['type']}〕 RICE={s['rice']}"}])
            if s["desc"]:
                content.append([{"tag": "text", "text": f"    ↳ {s['desc']}"}])
    # 🔍 迭代核验分组（存量高价值信号滚动复核，允许与往轮重复）
    if iters:
        content.append([{"tag": "text", "text": "🔍 迭代核验（存量高价值信号滚动复核，允许与往轮重复）"}])
        for i, s in enumerate(iters, 1):
            content.append([{"tag": "text",
                             "text": f"{i}. {s['name']} 〔{s['type']}〕 RICE={s['rice']}"}])
            if s["desc"]:
                content.append([{"tag": "text", "text": f"    ↳ {s['desc']}"}])
    content.append([{"tag": "text",
                     "text": f"📂 复盘：{CFG['report_dir']}/scan_round{round_n}_{dim}.md"}])
    return {"zh_cn": {"title": title, "content": content}}

def send_feishu(chat_id, content, idem):
    js = json.dumps(content, ensure_ascii=False)
    cmd = [LARK, "im", "+messages-send", "--as", "bot", "--chat-id", chat_id,
           "--msg-type", "post", "--content", js, "--idempotency-key", idem]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None, r.stderr[:300]
    try:
        o = json.loads(r.stdout)
    except Exception:
        return None, "parse-fail:" + r.stdout[:200]
    # 取 om_
    mid = None
    if isinstance(o, dict):
        mid = (o.get("message_id") or o.get("data", {}).get("message_id")
               or (o.get("data", {}).get("items", [{}])[0].get("message_id"))
               or o.get("data", {}).get("message", {}).get("message_id"))
    return mid, o

def batch_bitable(records):
    payload = json.dumps({"create_records": records}, ensure_ascii=False)
    cmd = [LARK, "base", "+record-batch-create", "--as", "bot",
           "--base-token", BASE_TOKEN, "--table-id", TABLE_ID, "--json", payload]
    r = subprocess.run(cmd, capture_output=True, text=True)
    ok = r.returncode == 0
    msg = r.stdout if ok else r.stderr  # 不截断，否则 record_id_list JSON 被切断
    return ok, msg

FP_FILE = os.path.join(HERE, "bitable_published.json")
def load_fp():
    if os.path.exists(FP_FILE):
        return json.load(open(FP_FILE))
    return {}
def save_fp(fp):
    json.dump(fp, open(FP_FILE, "w"), ensure_ascii=False, indent=2)

def main(round_n, date, only_dim=None):
    verdicts = load_verdicts(round_n)
    files = sorted(glob.glob(os.path.join(HERE, f"signals_r{round_n}_*.txt")))
    results = {"round": round_n, "date": date, "dims": {}}
    fp = load_fp()
    fp_r = fp.setdefault(str(round_n), {})
    n_dims = 0
    total_sent = 0
    bt_total = 0
    for f in files:
        dim = os.path.basename(f).replace(f"signals_r{round_n}_", "").replace(".txt", "")
        if only_dim and dim != only_dim:
            continue
        if dim not in DIM_CHAT:
            print(f"  ! 跳过未知维度 {dim}")
            continue
        n_dims += 1
        chat_id = DIM_CHAT[dim]
        sigs = parse_signals(f)
        if not sigs:
            continue
        recs = []
        vi = verdicts.get(dim)
        post = build_post(dim, sigs, round_n, vi)
        idem = f"round{round_n}-{dim}-{date}"
        mid, resp = send_feishu(chat_id, post, idem)
        status = "ok" if mid else "fail"
        if mid:
            total_sent += 1
        print(f"  [{dim}] {len(sigs)}条 -> 飞书 {status} om={mid}")
        for s in sigs:
            recs.append({
                "维度": DIM_CN.get(dim, dim),
                "周期": f"R{round_n}",
                "周期日期": f"{date[:4]}-{date[4:6]}-{date[6:]}",
                "专群chat_id": chat_id,
                "标题": s["name"],
                "外层信号摘要": s["desc"],
                "核心结论": (vi or {}).get("verdict", "待裁决"),
                "质量门": f"{ (vi or {}).get('p',0) }✅/{(vi or {}).get('w',0)}⚠️/{(vi or {}).get('f',0)}❌",
                "报告路径": f"{CFG['report_dir']}/scan_round{round_n}_{dim}.md",
                "飞书消息ID": mid or "",
            })
        # Bitable per-dim 指纹去重写入
        fp_d = fp_r.setdefault(dim, {})
        new_recs = [r for r in recs if fp_d.get(r["标题"]) is None]
        if new_recs:
            ok, msg = batch_bitable(new_recs)
            if ok:
                try:
                    o = json.loads(msg)
                    rids = o.get("data", {}).get("record_id_list", [])
                except Exception:
                    rids = []
                if not rids:
                    rids = re.findall(r"recv[A-Za-z0-9]+", msg)
                for r, rid in zip(new_recs, rids):
                    fp_d[r["标题"]] = rid
                save_fp(fp)
                bt_total += len(new_recs)
                print(f"     Bitable 新写 {len(new_recs)} 条")
            else:
                print(f"     Bitable FAIL: {msg[:200]}")
        else:
            print(f"     Bitable 已发布，跳过（指纹命中 {len(recs)}）")
        results["dims"][dim] = {"n": len(sigs), "om": mid, "status": status,
                                 "verdict": (vi or {}).get("verdict")}
    outp = os.path.join(HERE, f"push_r{round_n}_results.json")
    json.dump(results, open(outp, "w"), ensure_ascii=False, indent=2)
    print(f"✅ 轮 R{round_n} 发布完成：飞书 {total_sent}/{n_dims} 维 OK，Bitable 新写 {bt_total} 条。结果 -> {outp}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--date", type=str, required=True, help="YYYYMMDD")
    ap.add_argument("--dim", type=str, default=None, help="仅发指定维度(验证用)")
    a = ap.parse_args()
    main(a.round, a.date, a.dim)
