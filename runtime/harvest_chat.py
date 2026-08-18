#!/usr/bin/env python3
"""分页收割飞书专群历史消息（bot 身份，无需 user 授权）。

用法：
  python3 harvest_chat.py <chat_id> <out_json>
输出：包含 data.messages 全部历史 + 每条约化 _text / _sender_name / _time。

依赖：lark-cli（已配置 bot 凭证）。bot 身份即可读群历史，不需要 user 的 search:message 授权。
"""
import subprocess
import json
import re
import sys
import os

LARK = "lark-cli"


def call(chat_id, token):
    cmd = [LARK, "im", "+chat-messages-list", "--as", "bot",
           "--chat-id", chat_id, "--page-size", "50", "--format", "json",
           "--order", "desc", "--no-reactions"]
    if token:
        cmd += ["--page-token", token]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("lark-cli err: " + r.stderr[:300])
    return json.loads(r.stdout)


def extract_text(m):
    """把飞书消息 content 归一化为纯文本。

    content 可能是「字符串化的 JSON」，也可能直接是纯文本：
    - text:    {"text":"..."} 或扁平字符串
    - post:    扁平 markdown 字符串，或 {"title":..,"content":[[{text:..}]]} 嵌套结构
    - interactive/system/file: 非文本，尽量取可读片段
    """
    try:
        c = m.get("content")
        if isinstance(c, str):
            try:
                c = json.loads(c)
            except Exception:
                # 纯文本（扁平 markdown / 普通文本）→ 去标签
                return re.sub(r'<[^>]+>', ' ', c).strip()
        t = m.get("msg_type")
        if t == "text":
            if isinstance(c, dict):
                return c.get("text", "")
            return str(c)
        if t == "post":
            if isinstance(c, str):
                return re.sub(r'<[^>]+>', ' ', c).strip()
            parts = []
            for block in (c or {}).get("content", []):
                if isinstance(block, list):
                    for el in block:
                        if isinstance(el, dict) and "text" in el:
                            parts.append(el["text"])
            return "\n".join(parts)
        if t == "interactive":
            return json.dumps(c, ensure_ascii=False)[:600]
        return json.dumps(c, ensure_ascii=False)[:300]
    except Exception as e:
        return "[parse-fail:%s]" % e


def main():
    chat_id = sys.argv[1]
    out = sys.argv[2]
    all_msgs = []
    token = None
    page = 0
    while True:
        d = call(chat_id, token)
        data = d.get("data", {})
        msgs = data.get("messages", [])
        all_msgs.extend(msgs)
        page += 1
        has_more = data.get("has_more")
        token = data.get("page_token")
        print("page %d: +%d (total %d) has_more=%s" % (page, len(msgs), len(all_msgs), has_more))
        if not has_more or not token:
            break

    for m in all_msgs:
        m["_text"] = extract_text(m)
        m["_sender_name"] = (m.get("sender") or {}).get("name")
        m["_time"] = m.get("create_time")

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"chat_id": chat_id, "count": len(all_msgs), "messages": all_msgs},
                  f, ensure_ascii=False, indent=2)
    print("SAVED %s count=%d" % (out, len(all_msgs)))


if __name__ == "__main__":
    main()
