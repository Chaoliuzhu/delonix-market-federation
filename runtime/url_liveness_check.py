#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
URL 存活检测：扫描 signals_r<N>_*.txt 内嵌来源链接，curl 校验状态码，
输出失效清单（供发布前质检 / 复盘标注「来源不可访问」）。

用法：
  python3 url_liveness_check.py --round 23
  python3 url_liveness_check.py --round 23 --timeout 15
  python3 url_liveness_check.py --round 23 --fix        # 把失效 URL 回写标注（可选）

判定：
  200/301/302/308  → 可用
  其余（含 403/404/521/000 超时/ERR）→ 失效
"""
import argparse, glob, json, os, re, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
URL_RE = re.compile(r"https?://[^\s，。；）)、（\"'\]<>()]+")

# 视为可用的状态码
OK_CODES = {"200", "301", "302", "308"}


def extract_urls(text: str):
    raw = URL_RE.findall(text)
    out = []
    for u in raw:
        if "+" in u:
            # "urlA+http://urlB" 是真多链接；"url+中文描述" 截断中文
            for frag in u.split("+"):
                frag = frag.strip()
                if frag.startswith("http"):
                    out.append(frag)
        else:
            out.append(u.rstrip("，。；)、。"))
    seen = set()
    res = []
    for u in out:
        if u and u not in seen:
            seen.add(u)
            res.append(u)
    return res


def check_url(url: str, timeout: int) -> str:
    try:
        r = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120 Safari/537.36",
             "--max-time", str(timeout), "-L", url],
            capture_output=True, text=True,
        )
        return r.stdout.strip() or "000"
    except Exception as e:
        return f"ERR:{e}"


def main(round_n: int, timeout: int = 12):
    files = sorted(glob.glob(os.path.join(HERE, f"signals_r{round_n}_*.txt")))
    if not files:
        print(f"未找到 signals_r{round_n}_*.txt")
        return
    dead = []
    total = 0
    dim_bad = {}
    for f in files:
        dim = os.path.basename(f).replace(f"signals_r{round_n}_", "").replace(".txt", "")
        for line in open(f, encoding="utf-8"):
            if line.lstrip().startswith("#"):
                continue
            for u in extract_urls(line):
                total += 1
                code = check_url(u, timeout)
                if code not in OK_CODES:
                    dead.append({"dim": dim, "url": u, "code": code})
                    dim_bad[dim] = dim_bad.get(dim, 0) + 1
    outp = os.path.join(HERE, f"url_dead_r{round_n}.json")
    json.dump(dead, open(outp, "w"), ensure_ascii=False, indent=2)
    print(f"总计检测 {total} 个 URL，失效 {len(dead)} 个 → {outp}")
    if dead:
        print("\n失效清单（按维度）：")
        for dim in sorted(dim_bad):
            print(f"  [{dim}] {dim_bad[dim]} 个")
        for d in dead:
            print(f"  [{d['dim']}] {d['code']:>6}  {d['url']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--timeout", type=int, default=12, help="单 URL 超时秒数")
    a = ap.parse_args()
    main(a.round, a.timeout)
