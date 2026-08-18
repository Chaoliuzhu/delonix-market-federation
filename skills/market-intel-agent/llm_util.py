#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm_util.py · 共享 LLM 调用（OpenAI 兼容 /v1/chat/completions）

环境变量覆盖：
  MARKET_LLM_BASE_URL  (默认 http://127.0.0.1:4000，即本地 LiteLLM router)
  MARKET_LLM_MODEL     (默认 delonix-tokenhub-glm)
  MARKET_LLM_API_KEY   (默认 sk-local)

任一异常（网络/模型/解析）→ 返回 None，调用方必须优雅降级（绝不致命）。
"""
import json
import os
import sys
import urllib.request

DEFAULT_BASE = os.environ.get("MARKET_LLM_BASE_URL", "http://127.0.0.1:4000")
DEFAULT_MODEL = os.environ.get("MARKET_LLM_MODEL", "delonix-tokenhub-glm")
DEFAULT_KEY = os.environ.get("MARKET_LLM_API_KEY", "sk-local")


def call_llm(user_prompt, system=None, model=None, max_tokens=900,
             temperature=0.3, base_url=None, api_key=None, timeout=30):
    """返回 LLM 文本，失败返回 None。"""
    base = (base_url or DEFAULT_BASE).rstrip("/")
    mdl = model or DEFAULT_MODEL
    key = api_key or DEFAULT_KEY
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_prompt})
    payload = {
        "model": mdl,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
        return (resp.get("choices", [{}])[0].get("message", {}) or {}).get("content") or ""
    except Exception as e:
        sys.stderr.write(f"[llm_util] 调用失败：{e}\n")
        return None


def available():
    """快速探活：能否连到端点。"""
    try:
        req = urllib.request.Request(
            DEFAULT_BASE.rstrip("/") + "/health",
            headers={"Authorization": f"Bearer {DEFAULT_KEY}"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


if __name__ == "__main__":
    print("available:", available())
    print("test:", repr(call_llm("只回复两个字：连通", max_tokens=20)))
