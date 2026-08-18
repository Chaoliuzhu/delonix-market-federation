#!/usr/bin/env python3
"""沉淀层索引 v1（R4 迭代）· harvest 群历史 → SQLite FTS5 全文索引。

问题：10 个专群 harvest JSON 共 7.5MB+（corporate.json 单个 3.6MB / 2630 条），
      内层查证靠 grep 整文件，慢且无法按维度/时间/发言人切片，
      导致 sub-agent 偷懒跳内层、直接外层 WebSearch → 幻觉率上升。

方案：一次性建 FTS5 索引，之后毫秒级检索，支持 --dim / --since / --sender 过滤。

用法：
  python3 harvest_index.py build                       # 建/重建索引
  python3 harvest_index.py search "顺丰 协议"           # 全库检索
  python3 harvest_index.py search "长住" --dim extended --limit 10
  python3 harvest_index.py search "会议" --since 2026-06-01
  python3 harvest_index.py stats
"""
import os
import sys
import json
import re
import sqlite3
import argparse
import glob

HERE = os.path.dirname(os.path.abspath(__file__))
HARVEST_DIR = os.path.join(HERE, "harvest")
DB = os.path.join(HERE, "harvest_index.db")

# 维度别名 → 规范维度名（与 AGENT_FEDERATION.md 对齐）
DIM_ALIAS = {
    "mice": "mice", "seven": "mice", "七": "mice",
    "leisure": "leisure", "one": "leisure", "一": "leisure",
    "corporate": "corporate", "two": "corporate", "二": "corporate",
    "loyalty": "loyalty", "three": "loyalty", "三": "loyalty",
    "extended": "extended", "five": "extended", "五": "extended",
    "digital": "digital", "six": "digital", "六": "digital",
    "potentialsource": "potentialsource", "eight": "potentialsource",
    "broardsignal": "broardsignal", "nine": "broardsignal",
    "tmc": "tmc", "ten": "tmc",
    "composite": "composite",
}

# chat_id → 维度（权威映射，见 AGENT_FEDERATION.md）
# 同一个群可能落成多个文件名（如 corporate.json 与 harvest_oc_10122....json），
# 以 chat_id 为准 + msg_id 去重，避免同群被索引两遍。
CHAT2DIM = {
    "oc_65cb7557962c536363e1ecf183238dd5": "mice",
    "oc_3ec4543c324dd930762411f24bb69c12": "leisure",
    "oc_10122aa30da1d9bc64b365dadb3b6dbb": "corporate",
    "oc_f00114c109717481f66dec204b684f3a": "loyalty",
    "oc_976d2c67613069adab5e9d7c50aaf516": "extended",
    "oc_b2bfcebb35ea880085808e1d2fac258f": "digital",
    "oc_dd647b2d81a707fffb743bd6d6547580": "potentialsource",
    "oc_16bb4d1de93dfa1a068cb62894e55940": "broardsignal",
    "oc_222488ef4305c59a9a18dd5140e52a9f": "tmc",
    "oc_2c8ae1e5fb9efb46ac769f198f653006": "composite",
}


def norm_dim(d: str) -> str:
    if not d:
        return ""
    return DIM_ALIAS.get(d.strip().lower(), d.strip().lower())


def clean_text(msg: dict) -> str:
    """从飞书消息对象抽可检索文本。content 可能是 JSON 串或纯文本。"""
    parts = []
    t = msg.get("_text") or ""
    if t and not t.startswith("[parse-fail"):
        parts.append(t)
    c = msg.get("content") or ""
    if c:
        if c.lstrip().startswith("{"):
            try:
                obj = json.loads(c)
                parts.append(_walk_strings(obj))
            except Exception:
                parts.append(c)
        else:
            parts.append(c)
    s = "\n".join(p for p in parts if p)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _walk_strings(o) -> str:
    out = []
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("text", "content", "title", "tag"):
                out.append(_walk_strings(v))
            else:
                out.append(_walk_strings(v))
    elif isinstance(o, list):
        for v in o:
            out.append(_walk_strings(v))
    elif isinstance(o, str):
        out.append(o)
    return " ".join(x for x in out if x)


def tokenize_cjk(s: str) -> str:
    """FTS5 默认 unicode61 不切中文。用 bigram 展开保证中文可检索。"""
    s = s or ""
    toks = []
    for m in re.finditer(r"[\u4e00-\u9fff]+|[A-Za-z0-9][A-Za-z0-9._-]*", s):
        w = m.group(0)
        if re.match(r"[\u4e00-\u9fff]", w):
            toks.append(w)
            if len(w) >= 2:
                toks.extend(w[i:i + 2] for i in range(len(w) - 1))
        else:
            toks.append(w.lower())
    return " ".join(toks)


def build():
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE msg(
        id INTEGER PRIMARY KEY, dim TEXT, chat_id TEXT, msg_id TEXT,
        sender TEXT, ts TEXT, text TEXT)""")
    con.execute("""CREATE VIRTUAL TABLE msg_fts USING fts5(
        toks, content='')""")
    files = sorted(glob.glob(os.path.join(HARVEST_DIR, "*.json")))
    # 顺带纳入根目录零散 harvest（如 harvest_oc_*.json）
    files += sorted(glob.glob(os.path.join(HERE, "harvest_oc_*.json")))
    total, skipped = 0, 0
    seen_msg = set()
    for fp in files:
        base = os.path.basename(fp)
        file_dim = norm_dim(base.replace(".json", "").replace("harvest_", ""))
        try:
            with open(fp, encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            print(f"  !! skip {base}: {e}")
            continue
        msgs = d.get("messages", d if isinstance(d, list) else [])
        file_chat = d.get("chat_id", "") if isinstance(d, dict) else ""
        n, dup = 0, 0
        for m in msgs:
            if not isinstance(m, dict):
                continue
            cid = m.get("chat_id", file_chat)
            dim = CHAT2DIM.get(cid, file_dim)
            mid = m.get("message_id", "")
            if mid and mid in seen_msg:
                dup += 1
                continue
            if mid:
                seen_msg.add(mid)
            txt = clean_text(m)
            if not txt or len(txt) < 4:
                continue
            cur = con.execute(
                "INSERT INTO msg(dim,chat_id,msg_id,sender,ts,text) VALUES(?,?,?,?,?,?)",
                (dim, cid, mid, m.get("_sender_name", ""),
                 m.get("_time", m.get("create_time", "")), txt))
            con.execute("INSERT INTO msg_fts(rowid,toks) VALUES(?,?)",
                        (cur.lastrowid, tokenize_cjk(txt)))
            n += 1
        total += n
        skipped += dup
        print(f"  indexed {base:45s} {n:5d} msgs"
              + (f"  (跨文件去重 {dup})" if dup else ""))
    con.execute("CREATE INDEX idx_dim ON msg(dim)")
    con.execute("CREATE INDEX idx_ts ON msg(ts)")
    con.commit()
    con.close()
    print(f"\nDONE. {total} messages（跨文件去重丢弃 {skipped}）→ {DB}")


def _word_clause(w: str) -> str:
    """单个词 → FTS5 子句。中文按 bigram AND（保证词内连续）。"""
    if re.match(r"[\u4e00-\u9fff]", w):
        if len(w) == 1:
            return f'"{w}"'
        return "(" + " AND ".join(f'"{w[i:i+2]}"' for i in range(len(w) - 1)) + ")"
    return f'"{w.lower()}"'


def _fts_query(q: str, mode: str = "and") -> str:
    """自然语言查询 → FTS5 查询。

    R4 修复：首版把整句拆 bigram 后一律 AND 拼接，导致「用工 招工」这类
    近义词并列查询要求两词同时出现 → 0 命中（单查「用工」却有结果）。
    由 R4 潜在客源维度 sub-agent 实战发现。
    现按「词」为单位：词内 AND（保连续），词间可选 AND / OR。
    """
    words = re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9][A-Za-z0-9._-]*", q)
    if not words:
        return '""'
    clauses = [_word_clause(w) for w in words]
    joiner = " OR " if mode == "or" else " AND "
    return joiner.join(clauses)


def _run(con, q, mode, dim, limit, since, sender):
    sql = ("SELECT m.dim,m.sender,m.ts,m.text,m.msg_id FROM msg_fts f "
           "JOIN msg m ON m.id=f.rowid WHERE msg_fts MATCH ?")
    args = [_fts_query(q, mode)]
    if dim:
        sql += " AND m.dim=?"
        args.append(norm_dim(dim))
    if since:
        sql += " AND m.ts>=?"
        args.append(since)
    if sender:
        sql += " AND m.sender LIKE ?"
        args.append(f"%{sender}%")
    sql += " ORDER BY bm25(msg_fts) LIMIT ?"
    args.append(limit)
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.OperationalError as e:
        print(f"查询错误: {e}", file=sys.stderr)
        sys.exit(1)


def search(q, dim=None, limit=15, since=None, sender=None, full=False, any_mode=False):
    if not os.path.exists(DB):
        print("索引不存在，先跑: python3 harvest_index.py build", file=sys.stderr)
        sys.exit(1)
    con = sqlite3.connect(DB)
    mode = "or" if any_mode else "and"
    rows = _run(con, q, mode, dim, limit, since, sender)
    note = ""
    # AND 0 命中 → 自动降级 OR，避免近义词并列查询空手而归
    if not rows and mode == "and" and len(
            re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9][A-Za-z0-9._-]*", q)) > 1:
        rows = _run(con, q, "or", dim, limit, since, sender)
        note = "  ⚠️ AND 无命中，已自动降级 OR（任一词命中）"
    if not rows:
        print(f"0 hits for '{q}'" + (f" (dim={dim})" if dim else "")
              + "  —— 换个说法或去掉 --dim 试试")
        return
    print(f"# {len(rows)} hits for '{q}'" + (f" dim={dim}" if dim else "")
          + note + "\n")
    for dm, sd, ts, tx, mid in rows:
        body = tx if full else (tx[:300] + ("…" if len(tx) > 300 else ""))
        print(f"[{dm}] {ts} · {sd}\n  {body}\n  msg_id={mid}\n")
    con.close()


def stats():
    if not os.path.exists(DB):
        print("索引不存在，先跑 build")
        return
    con = sqlite3.connect(DB)
    print("总消息:", con.execute("SELECT count(*) FROM msg").fetchone()[0])
    print("\n按维度:")
    for dm, n, mn, mx in con.execute(
            "SELECT dim,count(*),min(ts),max(ts) FROM msg GROUP BY dim ORDER BY 2 DESC"):
        print(f"  {dm:18s} {n:5d}  {mn} ~ {mx}")
    print("\nTOP 发言人:")
    for sd, n in con.execute(
            "SELECT sender,count(*) FROM msg GROUP BY sender ORDER BY 2 DESC LIMIT 10"):
        print(f"  {(sd or '(未知)'):24s} {n}")
    con.close()


def main():
    ap = argparse.ArgumentParser(prog="harvest_index.py")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("build")
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--dim", default=None)
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--since", default=None)
    p.add_argument("--sender", default=None)
    p.add_argument("--full", action="store_true")
    p.add_argument("--any", action="store_true", dest="any_mode",
                   help="词间 OR（默认 AND，AND 无命中会自动降级 OR）")
    sub.add_parser("stats")
    a = ap.parse_args()
    if a.cmd == "build":
        build()
    elif a.cmd == "search":
        search(a.query, a.dim, a.limit, a.since, a.sender, a.full, a.any_mode)
    elif a.cmd == "stats":
        stats()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
