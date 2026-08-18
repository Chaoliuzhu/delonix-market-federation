#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证动态路由器：用真实场景 query 测试维度路由是否正确（证明『动态』可行）。"""
import router

TESTS = [
    ("最近天津央企有什么培训住宿机会", ["tmc", "two"]),
    ("竞品酒店在搞什么促销活动", ["six", "four", "one"]),
    ("怎么提升会员复购率", ["three"]),
    ("我们餐饮宴会能接哪些婚宴需求", ["four"]),
    ("天津有什么大型展会会议", ["seven"]),
    ("长住客外派人员住宿怎么拓", ["five"]),
    ("宏观上滨海有什么政策红利", ["broad"]),
    ("随便聊聊", None),  # 应落到 full 保底
]


def main():
    ok = 0
    for q, expect in TESTS:
        r = router.route(q)
        got = [m["key"] for m in r["matched"] if m["score"] > 0] if r["mode"] == "targeted" else ["ALL"]
        if expect is None:
            passed = r["mode"] == "full"
        else:
            passed = all(k in got for k in expect) and len(got) <= len(expect) + 1
        ok += 1 if passed else 0
        flag = "✅" if passed else "❌"
        print(f"{flag} [{r['mode']:9}] {q}")
        print(f"       → 选中: {got}")
        if expect is not None:
            print(f"       期望含: {expect}")
    print(f"\n通过 {ok}/{len(TESTS)} ｜ 路由器按 query 动态选维度，而非固定全量。")


if __name__ == "__main__":
    main()
