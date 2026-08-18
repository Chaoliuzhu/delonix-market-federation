#!/usr/bin/env python3
"""检索扩展器 v1（R4 迭代）· 外层 WebSearch 查询矩阵自动生成。

问题：R1–R3 各维度 SKILL 里外层检索词是**硬编码 3 条**，
      导致每轮召回同一批源、增量枯竭（R3 已现「强印证 R2 主线」而非新发现）。

方案：查询 = 维度模板 × 地理锚点 × 时间窗 × 实体别名 × 竞品 × 情报体裁，
      按轴组合生成候选，去掉历史已跑过的（round_queries.json 记录），
      输出本轮建议查询词。

用法：
  python3 query_expander.py two --limit 12
  python3 query_expander.py mice --limit 10 --axis policy,competitor
  python3 query_expander.py --list
  python3 query_expander.py two --record "实际跑过的查询词"   # 记录已用，下轮自动避开
"""
import os
import json
import argparse
import random
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(HERE, "round_queries.json")

# 地理轴（锚定天津瑞湾开元名都）
GEO = ["天津滨海新区", "天津经开区 泰达", "天津港 新港", "于家堡 金融区",
       "天津保税区", "滨海新区 中心商务区"]
GEO_SPILL = ["北京 天津 溢出", "京津冀"]

NOW = datetime.date.today()
YEAR = NOW.year
# 时间窗：R3 截止 2026-08-03，R4 只要增量
TIME = [f"{YEAR}年{NOW.month}月", f"{YEAR}", f"{YEAR}下半年", "最新"]

# 情报体裁轴
GENRE = {
    "policy": ["政策", "规划", "支持措施", "补贴", "十五五"],
    "investment": ["签约", "落户", "投产", "开工", "总部落地", "亿元项目"],
    "competitor": ["酒店 竞品", "新开业 酒店", "洲际 万丽 丽怡", "价格 策略"],
    "demand": ["招标", "采购", "询价", "RFP", "框架协议"],
    "event": ["会议", "论坛", "展会", "年会", "赛事"],
    "trend": ["行业趋势", "报告", "白皮书", "数据"],
    "ai": ["AI Agent", "智能体", "自动化", "数字化"],
}

# 维度模板：核心词 + 默认体裁轴 + 竞品/实体别名
DIMS = {
    "mice": {
        "cn": "MICE 会议会展（七）",
        "core": ["会议会展", "会展场馆", "企业年会", "论坛承办", "展会档期"],
        "axes": ["event", "demand", "policy"],
        "entities": ["滨海国际会展中心", "泰达国际会展", "国家会展中心天津"],
        "competitor": ["于家堡洲际", "万丽泰达", "希尔顿滨海"],
    },
    "one": {
        "cn": "休闲度假（一）",
        "core": ["亲子 度假", "温泉 酒店", "周边游", "夜游 消费", "文旅 消费券"],
        "axes": ["policy", "event", "trend"],
        "entities": ["东疆湾", "极地海洋公园", "航母主题公园", "国家海洋博物馆"],
        "competitor": ["滨海 亲子酒店", "天津 温泉度假"],
    },
    "two": {
        "cn": "企业协议（二）",
        "core": ["企业 落户", "总部 迁入", "差旅 协议", "央企 二级公司", "开票 企业"],
        "axes": ["investment", "policy", "demand"],
        "entities": ["中交", "中船", "中海油", "国家管网", "顺丰", "诺和诺德"],
        "competitor": ["商旅 协议价", "企业 差旅 平台"],
    },
    "three": {
        "cn": "会员增购（三）",
        "core": ["会员 体系", "付费会员", "积分 权益", "私域 复购", "CLV"],
        "axes": ["trend", "competitor", "ai"],
        "entities": ["百达屋", "开元 会员", "亚朵", "华住会", "IHG 优悦会"],
        "competitor": ["亚朵 会员", "洲际 会员 拉新", "抖音 酒店 会员"],
    },
    "five": {
        "cn": "长住/服务式公寓（五）",
        "core": ["长租 公寓", "服务式公寓", "月租 房", "外派 住宿", "项目组 长住"],
        "axes": ["investment", "competitor", "demand"],
        "entities": ["蘭寓", "龙湖冠寓", "泊寓", "雅诗阁"],
        "competitor": ["滨海 公寓 出租", "塘沽 月租 酒店"],
    },
    "six": {
        "cn": "数字渠道/GEO（六）",
        "core": ["OTA 政策", "直销 官网", "小程序 私域", "抖音 本地生活", "AI 搜索 推荐"],
        "axes": ["ai", "trend", "competitor"],
        "entities": ["携程", "美团", "抖音", "飞猪", "小红书"],
        "competitor": ["酒店 GEO 优化", "AI 搜索 酒店 曝光"],
    },
    "potentialsource": {
        "cn": "潜在客源（八）",
        "core": ["新建 基地", "项目 开工 用工", "产业园 招工", "外派 团队", "工程 驻场"],
        "axes": ["investment", "policy"],
        "entities": ["中远海运重工", "大船天津", "一汽丰田", "海油工程"],
        "competitor": [],
    },
    "broardsignal": {
        "cn": "潜在广域信号（九）",
        "core": ["区域 经济 数据", "交通 规划 地铁", "口岸 免签", "消费 复苏", "人口 流入"],
        "axes": ["policy", "trend", "investment"],
        "entities": ["滨海 地铁 Z4", "B1 线", "邮轮母港", "天津港 吞吐量"],
        "competitor": [],
    },
    "tmc": {
        "cn": "TMC 订单（十）",
        "core": ["商旅 集采", "差旅 管理 平台", "TMC 招标", "费控 一体化", "协议酒店 入围"],
        "axes": ["demand", "ai", "trend"],
        "entities": ["中航服", "携程商旅", "分贝通", "同程商旅", "合思"],
        "competitor": ["央企 差旅 集采", "国企 酒店 集采"],
    },
    "four": {
        "cn": "餐饮宴会（四）· PAUSED",
        "core": ["宴会 婚宴", "团餐 承办", "餐饮 外包"],
        "axes": ["demand", "trend"],
        "entities": [],
        "competitor": [],
    },
}


def load_hist() -> dict:
    if os.path.exists(HIST):
        with open(HIST, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_hist(h: dict):
    with open(HIST, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=2)


def gen(dim: str, limit: int, axes=None, seed=None):
    if dim not in DIMS:
        raise SystemExit(f"未知维度 {dim}；可用: {', '.join(DIMS)}")
    d = DIMS[dim]
    use_axes = axes or d["axes"]
    hist = load_hist()
    used = set(hist.get(dim, []))
    rnd = random.Random(seed if seed is not None else f"{dim}-{NOW.isoformat()}")

    cands = []
    # 轴1：核心词 × 地理 × 时间
    for c in d["core"]:
        for g in GEO[:4]:
            cands.append(f"{g} {c} {TIME[0]}")
            cands.append(f"{g} {c} {TIME[1]}")
    # 轴2：核心词 × 体裁
    for c in d["core"]:
        for ax in use_axes:
            for gterm in GENRE.get(ax, [])[:3]:
                cands.append(f"天津滨海 {c} {gterm} {YEAR}")
    # 轴3：实体别名（做实体级追踪，最容易出增量）
    for e in d.get("entities", []):
        cands.append(f"{e} {YEAR}年 最新 动态")
        cands.append(f"{e} 天津 {YEAR} 签约 OR 落地 OR 招标")
    # 轴4：竞品
    for c in d.get("competitor", []):
        cands.append(f"{c} {YEAR} 天津滨海")
    # 轴5：北京溢出
    for g in GEO_SPILL:
        cands.append(f"{g} {d['core'][0]} {YEAR}")
    # 轴6：英文（外层标杆，避免中文源同质化）
    cands.append(f"Tianjin Binhai {dim} hotel demand {YEAR}")

    # 去重 + 去历史
    seen, fresh, repeat = set(), [], []
    for q in cands:
        q = " ".join(q.split())
        if q in seen:
            continue
        seen.add(q)
        (repeat if q in used else fresh).append(q)

    rnd.shuffle(fresh)
    out = fresh[:limit]
    if len(out) < limit:
        out += repeat[: limit - len(out)]
    return d, out, len(cands), len(used)


def main():
    ap = argparse.ArgumentParser(prog="query_expander.py")
    ap.add_argument("dim", nargs="?")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--axis", default=None, help="逗号分隔，如 policy,competitor")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--record", default=None, help="记录实际跑过的查询（逗号分隔）")
    ap.add_argument("--seed", default=None)
    a = ap.parse_args()

    if a.list or not a.dim:
        print("可用维度：")
        for k, v in DIMS.items():
            print(f"  {k:16s} {v['cn']}  axes={v['axes']}")
        return

    if a.record:
        h = load_hist()
        h.setdefault(a.dim, [])
        for q in a.record.split(","):
            q = q.strip()
            if q and q not in h[a.dim]:
                h[a.dim].append(q)
        save_hist(h)
        print(f"recorded {len(a.record.split(','))} queries for {a.dim} "
              f"(history now {len(h[a.dim])})")
        return

    axes = a.axis.split(",") if a.axis else None
    d, out, ncand, nused = gen(a.dim, a.limit, axes, a.seed)
    print(f"# {d['cn']} · 查询矩阵（候选 {ncand} / 历史已用 {nused} / 本轮建议 {len(out)}）\n")
    for i, q in enumerate(out, 1):
        print(f"{i:2d}. {q}")
    print("\n# 跑完后记录，下轮自动避开：")
    print(f'#   python3 query_expander.py {a.dim} --record "查询1,查询2"')


if __name__ == "__main__":
    main()
