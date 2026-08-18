#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
动态市场情报 Agent · 维度路由器（MVP 核心）

把「每天固定全量跑 10 维」改为「按自然语言查询动态选维度」。
这是从『死的 prompt/skill』走向『动态 Agent』的关键一步：
- 能力元数据在 capabilities.json（维度/关键词/检索模板，不绑瑞湾）
- 酒店专属数据（群ID/地理/对接人/Bitable）外置到 hotel_config.yaml
- 本路由器接收 query → 规则 / LLM 语义选维度 → 返回「该跑哪些维度 + 为什么」

路由模式：
  targeted  → 命中维度，只跑相关维度（省 token、更聚焦）
  full      → 未识别，保底全量（或主 Agent 追问澄清）

LLM 语义路由（--use-llm）：
  调用 OpenAI 兼容 /v1/chat/completions（本地 LiteLLM router 或任意兼容端点）。
  任一异常（网络/模型/解析失败）自动降级为规则路由，绝不致命。
  环境变量可覆盖：MARKET_LLM_BASE_URL / MARKET_LLM_MODEL / MARKET_LLM_API_KEY

用法：
  python3 router.py "最近天津央企有什么培训住宿机会"
  python3 router.py "竞品在搞什么促销" --geo "天津滨海"
  python3 router.py "帮我看会员复购怎么提升" --json
  python3 router.py "最近有啥会展" --use-llm --llm-model delonix-m3
"""
import argparse
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def load_caps():
    with open(os.path.join(HERE, "capabilities.json"), encoding="utf-8") as f:
        return json.load(f)["capabilities"]


def load_config(config_path=None):
    """酒店专属数据外置。找不到则返回占位，不致命。"""
    p = config_path or os.path.join(HERE, "hotel_config.yaml")
    if not os.path.exists(p):
        return {"hotel": "UNSET", "geo": "{geo}", "anchor": "", "feishu_chat": {}, "bitable": ""}
    cfg = {}
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        if " #" in line:  # 去行内注释，避免污染取值
            line = line.split(" #", 1)[0].rstrip()
        k, v = line.split(":", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def rule_route(query: str, caps):
    q = query.lower()
    hits = []
    for c in caps:
        score = 0
        for kw in c["keywords"]:
            if kw.lower() in q:
                score += 1
        if score:
            hits.append({"key": c["key"], "name": c["name"],
                         "score": score, "scenes": c.get("scenes", [])})
    hits.sort(key=lambda x: -x["score"])
    return hits


def _extract_json(text):
    """容错解析 LLM 返回的 JSON（模型可能包裹在 ```json 或混入文字）。"""
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # 去掉 markdown 代码块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 退而求其次：抓取第一个 {...}
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


def llm_route(query: str, geo: str = None):
    """真实调用 LLM 做语义路由。失败返回 None（交由规则兜底）。"""
    caps = load_caps()
    base = os.environ.get("MARKET_LLM_BASE_URL", "http://127.0.0.1:4000").rstrip("/")
    model = os.environ.get("MARKET_LLM_MODEL", "delonix-tokenhub-glm")
    key = os.environ.get("MARKET_LLM_API_KEY", "sk-local")

    dim_desc = "\n".join(
        f"- {c['key']}: {c['name']}（关键词：{','.join(c['keywords'])}）" for c in caps
    )
    system = (
        "你是酒店市场情报路由助手。用户用自然语言询问酒店市场机会，"
        "你需要判断该查询应触发哪些业务维度。\n"
        f"可选维度如下：\n{dim_desc}\n\n"
        "只输出一个 JSON 对象，不要任何额外文字，格式严格如下：\n"
        '{"mode":"targeted" 或 "full","dims":["维度key1","维度key2"],"reason":"简短中文理由"}。\n'
        '若查询明确指向某些维度，mode 用 targeted 并列出对应 dims；'
        '若无法判断或明显需要全盘扫描，mode 用 full 并列出全部维度 key。'
    )
    user = f"地理锚点：{geo or '未指定'}\n用户查询：{query}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 400,
    }
    try:
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            resp = json.loads(r.read().decode("utf-8"))
        content = resp["choices"][0]["message"]["content"] or ""
    except Exception as e:
        sys.stderr.write(f"[llm_route] 调用失败，降级规则路由：{e}\n")
        return None

    data = _extract_json(content)
    if not data:
        sys.stderr.write("[llm_route] 返回非 JSON，降级规则路由\n")
        return None

    known = {c["key"] for c in caps}
    dims = [d for d in data.get("dims", []) if d in known]
    if data.get("mode") == "full" or not dims:
        return {
            "mode": "full",
            "matched": [{"key": c["key"], "name": c["name"], "score": 0, "scenes": []} for c in caps],
            "reason": data.get("reason", "LLM 建议全量扫描"),
            "query": query, "geo": geo, "llm": True,
        }
    matched = [{"key": c["key"], "name": c["name"], "score": 1, "scenes": c.get("scenes", [])}
               for c in caps if c["key"] in dims]
    return {
        "mode": "targeted",
        "matched": matched,
        "reason": data.get("reason", "LLM 语义匹配"),
        "query": query, "geo": geo, "llm": True,
    }


def route(query: str, geo: str = None, use_llm: bool = False):
    caps = load_caps()
    if use_llm:
        r = llm_route(query, geo)
        if r:
            return r
    # 规则兜底
    hits = rule_route(query, caps)
    if not hits:
        return {
            "mode": "full",
            "matched": [{"key": c["key"], "name": c["name"], "score": 0, "scenes": []} for c in caps],
            "reason": "未识别到具体维度关键词，默认全量扫描（或主 Agent 追问澄清）",
            "query": query, "geo": geo,
        }
    return {
        "mode": "targeted",
        "matched": hits,
        "reason": f"根据查询关键词匹配到 {len(hits)} 个相关维度",
        "query": query, "geo": geo,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", help="自然语言查询")
    ap.add_argument("--geo", default=None, help="地理锚点覆盖（可选）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--config", default=None, help="hotel_config.yaml 路径")
    ap.add_argument("--use-llm", action="store_true", help="启用 LLM 语义路由")
    ap.add_argument("--llm-model", default=None, help="覆盖 LLM 模型名（同时设 MARKET_LLM_MODEL）")
    a = ap.parse_args()

    if a.llm_model:
        os.environ["MARKET_LLM_MODEL"] = a.llm_model

    cfg = load_config(a.config)
    geo = a.geo or cfg.get("geo", "{geo}")
    r = route(a.query, geo=geo, use_llm=a.use_llm)

    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return

    tag = "（LLM语义）" if r.get("llm") else "（关键词）"
    print(f"查询: {a.query}")
    print(f"模式: {r['mode']}{tag} ｜ 理由: {r['reason']}")
    print(f"地理: {geo}")
    print("将调用维度:")
    for m in r["matched"]:
        star = "★" if m["score"] > 0 else " "
        print(f"  {star} {m['name']}({m['key']}) 匹配分={m['score']}")


if __name__ == "__main__":
    main()
