#!/usr/bin/env python3
"""持久信号注册表去重引擎 v2（R4 迭代）。

v1(dedup.py) 的问题：key = sha1(小写去标点) 精确哈希。
  「中交一航局」「中交第一航务工程局」「中交一航务局」「中交一航科技」「中交集团」
  在 v1 眼里是 5 个完全不同的实体 —— R4 种子里就真的躺着这 5 条。

v2 三层判定：
  L1 精确  : 兼容 v1 sha1 key，历史数据零迁移成本
  L2 规范  : 剥离企业后缀/地域修饰 + 别名词典 → canonical key
  L3 模糊  : char-bigram Jaccard 相似度 + 包含关系
  L4 集团  : 央企集团前缀识别 → 不合并，建 parent 父子关系（二级公司是独立签约主体）

用法：
  python3 dedup2.py check  "<名>|<geo>|<type>"
  python3 dedup2.py add    "<名>|<geo>|<type>" --dim tmc --round 4 --note "..." [--rice 14] [--needs-data] [--parent "中交集团"]
  python3 dedup2.py audit                      # 全表扫疑似重复簇（只报告，不改数据）
  python3 dedup2.py stats
"""
import sys
import json
import hashlib
import re
import os
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
REG = os.path.join(HERE, "signal_registry.json")
ALIAS_FILE = os.path.join(HERE, "entity_aliases.json")

FUZZY_THRESHOLD = 0.72

# 企业后缀 / 地域修饰，归一化时剥离
CORP_SUFFIX = [
    "股份有限公司", "有限责任公司", "有限公司", "集团有限公司", "总公司", "分公司",
    "有限合伙", "合伙企业", "集团", "公司", "厂", "研究院", "研究所", "中心",
    "工程局", "工程有限公司", "建设集团", "控股", "实业",
]
GEO_MODIFIER = [
    "天津", "滨海新区", "滨海", "泰达", "开发区", "经开区", "保税区",
    "新港", "于家堡", "塘沽", "北京", "中国",
]

# 央企/大集团前缀 → 用于 L4 父子关系识别（不做强合并）
GROUP_PREFIX = [
    "中交", "中铁建", "中铁", "中建", "中海油", "中石油", "中石化", "中船", "中远海运",
    "中远", "国家管网", "国家能源", "国家电网", "华能", "华为", "顺丰", "一汽",
    "中国资源循环", "招商局", "中化", "中粮", "航天科技", "航天科工", "兵器工业",
]

FULL2HALF = {ord(c): ord(c) - 0xFEE0 for c in
             "".join(chr(i) for i in range(0xFF01, 0xFF5F))}

# R4 修复：历史轮 spec 带编号前缀（"A8·文旅局培训会议" / "R3-W4·中国中铁集采" /
# "S1 惠博普"），v2 首版未剥离 → 同一实体换个编号就判 NEW（假新信号）。
# 由 R4 休闲(一) 维度 sub-agent 实战发现。
CODE_PREFIX_RE = re.compile(
    r"^(?:R\d+[-_]?[A-Za-z]{0,3}\d*|[A-Za-z]{1,3}[-_]?\d{1,3})\s*[·•\.\:：、\-—]\s*"
)
PAREN_RE = re.compile(r"[（(\[【][^）)\]】]*[）)\]】]")


def _strip_code_prefix(name: str) -> str:
    s = name.strip()
    for _ in range(3):                       # 允许 "R3-W4·S1·xxx" 多层
        s2 = CODE_PREFIX_RE.sub("", s).strip()
        if s2 == s or not s2:
            break
        s = s2
    return s or name.strip()


def _norm_basic(s: str) -> str:
    s = s.translate(FULL2HALF).lower()
    s = re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE)
    return s


def load_aliases() -> dict:
    """别名词典：{别名归一化: 规范名}。文件不存在则用内置种子。"""
    if os.path.exists(ALIAS_FILE):
        with open(ALIAS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    else:
        raw = SEED_ALIASES
    out = {}
    for canon, alist in raw.items():
        out[_norm_basic(canon)] = canon
        for a in alist:
            out[_norm_basic(a)] = canon
    return out


SEED_ALIASES = {
    "中交一航局": ["中交第一航务工程局", "中交一航务局", "中交一航", "一航局",
                   "中交第一航务工程局有限公司"],
    "中交集团": ["中国交通建设集团", "中国交建", "中交建"],
    "中海油": ["中国海洋石油", "中海石油", "中海油能源", "渤海油田", "中国海油"],
    "中船集团": ["中国船舶集团", "中船", "中船天津", "中国船舶"],
    "顺丰": ["顺丰集团", "顺丰速运", "顺丰数字物流总部"],
    "国家管网": ["国家石油天然气管网集团", "国家管网集团", "国家管网天津运营中心"],
    "天津港": ["天津港集团", "天津港股份"],
    "中铁十六局": ["中铁十六局集团", "中铁十六局有限公司"],
    "华为": ["华为技术", "华为天津研究所"],
    "携程": ["携程旅行", "Trip.com", "携程集团"],
    "美团": ["美团点评", "美团旅行"],
    "飞猪": ["阿里飞猪", "飞猪旅行"],
    "分贝通": ["分贝通费控"],
    "中航服": ["中国航空服务", "中航服商旅"],
}
# 注意：SEED_ALIASES 仅作「文件缺失」兜底。正式维护请在 entity_aliases.json
# （见 ENTITY_ALIAS_REGISTRY.md，10 Agent 共用 Alias Governance 底座，WB-LC-04）。
# load_aliases() 优先读 entity_aliases.json。新增别名改那个文件，勿改此处。

GEO_BUCKET = {
    "binhai": ["滨海", "泰达", "新港", "于家堡", "保税区", "港口", "塘沽", "开发区",
               "经开区", "东疆"],
    "beijing_spill": ["北京溢出", "北京"],
    "downgrade": ["津南", "国展", "国家会展中心", "降级", "远距"],
}


def geo_bucket(geo: str) -> str:
    g = _norm_basic(geo)
    for bucket, kws in GEO_BUCKET.items():
        for kw in kws:
            if _norm_basic(kw) in g:
                return bucket
    return g or "unspecified"


def canonical_name(name: str, aliases: dict) -> str:
    """名称规范化：剥编号前缀 → 剥括号补注 → 别名映射 → 剥地域修饰 → 剥企业后缀。"""
    raw = _strip_code_prefix(name)
    depar = PAREN_RE.sub("", raw).strip() or raw
    n = _norm_basic(depar)
    if n in aliases:
        return _norm_basic(aliases[n])
    if _norm_basic(raw) in aliases:
        return _norm_basic(aliases[_norm_basic(raw)])
    core = n
    for suf in sorted(CORP_SUFFIX, key=len, reverse=True):
        s = _norm_basic(suf)
        if core.endswith(s) and len(core) > len(s) + 1:
            core = core[: -len(s)]
            break
    for mod in sorted(GEO_MODIFIER, key=len, reverse=True):
        m = _norm_basic(mod)
        if core.startswith(m) and len(core) > len(m) + 1:
            core = core[len(m):]
            break
        if core.endswith(m) and len(core) > len(m) + 1:
            core = core[: -len(m)]
            break
    if core in aliases:
        return _norm_basic(aliases[core])
    return core


def group_of(name: str) -> str:
    n = _norm_basic(name)
    for g in sorted(GROUP_PREFIX, key=len, reverse=True):
        if n.startswith(_norm_basic(g)):
            return g
    return ""


def bigrams(s: str) -> set:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def similarity(a: str, b: str) -> float:
    A, B = bigrams(a), bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


# L3.5 bigram 语义层（R10 新增·跨维跨轮同事实检测）
L35_STOP = {"滨海", "全国", "天津", "降级", "新区", "项目", "中心", "服务", "酒店",
            "企业", "建设", "海新", "有限", "公司", "集团", "管理", "工程", "采购",
            "招标", "中标", "信号", "机会", "需求", "市场"}


def _spec_bigrams(spec_str: str) -> set:
    """spec 名称段 → bigram 集合 ∪ 数字 token（剔除高频停用词）。
    模块级函数，check() 和 add() 共用，确保入库与查重一致。"""
    nm = re.split(r"[|｜]", spec_str)[0]
    cn = "".join(re.findall(r"[\u4e00-\u9fff]+", nm))
    bg = {cn[i:i + 2] for i in range(len(cn) - 1)}
    nums = set(re.findall(r"\d{2,}", nm))
    return (bg - L35_STOP) | nums


def _spec_overlap(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def parse_spec(spec: str):
    parts = [p.strip() for p in spec.split("|")]
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1], parts[2]


def key_of(spec: str) -> str:
    """v1 兼容精确 key。"""
    parts = [_norm_basic(p) for p in spec.split("|")]
    while len(parts) < 3:
        parts.append("")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def ckey_of(spec: str, aliases: dict) -> str:
    """v2 规范 key。"""
    name, geo, typ = parse_spec(spec)
    c = canonical_name(name, aliases)
    return hashlib.sha1(
        f"{c}|{geo_bucket(geo)}|{_norm_basic(typ)}".encode()
    ).hexdigest()[:16]


def load() -> dict:
    with open(REG, encoding="utf-8") as f:
        return json.load(f)


def save(d: dict) -> None:
    with open(REG, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def check(spec: str, verbose: bool = True):
    d = load()
    aliases = load_aliases()
    k = key_of(spec)
    ck = ckey_of(spec, aliases)
    name, geo, typ = parse_spec(spec)
    cname = canonical_name(name, aliases)
    gb = geo_bucket(geo)
    grp = group_of(name)

    # L1 精确
    for e in d["entries"]:
        if e["key"] == k:
            if verbose:
                print(f"SEEN(L1-exact) key={k} dim={e['dim']} status={e['status']} "
                      f"round={e['first_seen_round']} note={e.get('note','')}")
            return "SEEN", e

    # L2 规范
    for e in d["entries"]:
        e_ck = e.get("ckey") or ckey_of(e["spec"], aliases)
        if e_ck == ck:
            if verbose:
                print(f"SEEN(L2-canonical) ~ '{e['spec']}' dim={e['dim']} "
                      f"round={e['first_seen_round']} | canon='{cname}' geo={gb}")
            return "SEEN", e

    # L3 模糊
    best, best_sim = None, 0.0
    for e in d["entries"]:
        e_name, e_geo, e_typ = parse_spec(e["spec"])
        if _norm_basic(e_typ) != _norm_basic(typ):
            continue
        if geo_bucket(e_geo) != gb:
            continue
        e_c = canonical_name(e_name, aliases)
        sim = similarity(cname, e_c)
        if (cname and e_c and (cname in e_c or e_c in cname)
                and min(len(cname), len(e_c)) >= 3):
            sim = max(sim, 0.85)
        if sim > best_sim:
            best, best_sim = e, sim
    if best is not None and best_sim >= FUZZY_THRESHOLD:
        if verbose:
            print(f"FUZZY({best_sim:.2f}) ~ '{best['spec']}' dim={best['dim']} "
                  f"round={best['first_seen_round']} → 人工判定是否同一实体")
        return "FUZZY", best

    # L3.5 bigram 语义层（调用模块级函数，与 add() 入库一致）
    spec_bg = _spec_bigrams(spec)
    if len(spec_bg) >= 3:  # token 太少不比
        best35, best35_ov = None, 0.0
        for e in d["entries"]:
            # 不与自己比（同 spec 同 key）
            if e.get("key") == k:
                continue
            # 优先用持久化的 bigram_fingerprint，否则实时算
            e_bg = set(e.get("bigram_fingerprint", [])) if e.get("bigram_fingerprint") else _spec_bigrams(e.get("spec", ""))
            if len(e_bg) < 3:
                continue
            ov = _spec_overlap(spec_bg, e_bg)
            if ov > best35_ov:
                best35, best35_ov = e, ov
        if best35 is not None and best35_ov >= 0.6:
            if verbose:
                print(f"FUZZY(L3.5-bigram {best35_ov:.2f}) ~ '{best35['spec']}' "
                      f"dim={best35['dim']} round={best35['first_seen_round']} "
                      f"→ 同事实不同措辞，建议标 transferred")
            return "FUZZY", best35

    # L4 集团父子
    if grp:
        sibs = [e for e in d["entries"]
                if group_of(parse_spec(e["spec"])[0]) == grp
                and geo_bucket(parse_spec(e["spec"])[1]) == gb]
        if sibs:
            if verbose:
                print(f"NEW(L4-same-group '{grp}') key={k} ckey={ck} "
                      f"| 同集团已有 {len(sibs)} 条: "
                      f"{', '.join(parse_spec(s['spec'])[0] for s in sibs[:5])}"
                      f"{' ...' if len(sibs) > 5 else ''}")
                print("  → 建议 --parent 建父子关系（二级公司是独立签约主体，不合并）")
            return "NEW_SAME_GROUP", sibs
    if verbose:
        print(f"NEW key={k} ckey={ck} canon='{cname}' geo={gb}")
    return "NEW", None


def add(spec, dim, round_n, note="", rice=None, needs_data=False,
        parent="", cross_dim=None, force=False):
    d = load()
    aliases = load_aliases()
    status, hit = check(spec, verbose=False)
    if status in ("SEEN",) and not force:
        print(f"EXISTS({status}) '{hit['spec']}' dim={hit['dim']} "
              f"round={hit['first_seen_round']}; skip（--force 可强制）")
        return
    if status == "FUZZY" and not force:
        print(f"FUZZY-BLOCKED ~ '{hit['spec']}'；确认非同一实体请加 --force")
        return
    _name, _geo, _typ = parse_spec(spec)
    entry = {
        "key": key_of(spec),
        "ckey": ckey_of(spec, aliases),
        "canonical_name": canonical_name(_name, aliases),  # v2.1 持久化
        "spec": spec,
        "dim": dim,
        "status": "active",
        "first_seen_round": round_n,
        "note": note,
    }
    # v2.1 持久化 bigram_fingerprint（L3.5 语义层底座）
    _name, _geo, _typ = parse_spec(spec)
    _bg = _spec_bigrams(spec)
    if _bg:
        entry["bigram_fingerprint"] = sorted(list(_bg))
    if rice is not None:
        entry["rice"] = rice
    if needs_data:
        entry["needs_data"] = True
    if parent:
        entry["parent"] = parent
    if cross_dim:
        entry["cross_dim"] = cross_dim
    grp = group_of(parse_spec(spec)[0])
    if grp:
        entry["group"] = grp
    d["entries"].append(entry)
    d["updated_round"] = max(d.get("updated_round", 0), round_n)
    save(d)
    print(f"ADDED key={entry['key']} ckey={entry['ckey']} dim={dim} round={round_n}"
          + (f" group={grp}" if grp else ""))


def audit():
    """全表扫疑似重复簇 + 集团簇。只报告，不改数据。"""
    d = load()
    aliases = load_aliases()
    es = d["entries"]
    print(f"# 注册表审计 · {len(es)} 条\n")

    # 规范 key 碰撞
    by_ck = {}
    for e in es:
        ck = e.get("ckey") or ckey_of(e["spec"], aliases)
        by_ck.setdefault(ck, []).append(e)
    dup_ck = {k: v for k, v in by_ck.items() if len(v) > 1}
    print(f"## L2 规范键碰撞（确定重复）：{len(dup_ck)} 簇")
    for k, v in dup_ck.items():
        print(f"  [{k}] " + " || ".join(f"{x['spec']}({x['dim']}/R{x['first_seen_round']})"
                                        for x in v))

    # 模糊簇
    print(f"\n## L3 模糊疑似（≥{FUZZY_THRESHOLD}，需人工判定）")
    seen_pairs = set()
    n_fuzzy = 0
    for i, a in enumerate(es):
        an, ag, at = parse_spec(a["spec"])
        ac = canonical_name(an, aliases)
        for b in es[i + 1:]:
            bn, bg, bt = parse_spec(b["spec"])
            if _norm_basic(at) != _norm_basic(bt):
                continue
            if geo_bucket(ag) != geo_bucket(bg):
                continue
            bc = canonical_name(bn, aliases)
            if (a.get("ckey") or ckey_of(a["spec"], aliases)) == \
               (b.get("ckey") or ckey_of(b["spec"], aliases)):
                continue
            sim = similarity(ac, bc)
            if ac and bc and (ac in bc or bc in ac) and min(len(ac), len(bc)) >= 3:
                sim = max(sim, 0.85)
            if sim >= FUZZY_THRESHOLD:
                pair = tuple(sorted([a["key"], b["key"]]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                n_fuzzy += 1
                print(f"  {sim:.2f}  {a['spec']}({a['dim']})  <~>  {b['spec']}({b['dim']})")
    if n_fuzzy == 0:
        print("  （无）")

    # 集团簇
    print("\n## L4 集团簇（父子关系候选，不应合并）")
    by_grp = {}
    for e in es:
        g = group_of(parse_spec(e["spec"])[0])
        if g:
            by_grp.setdefault(g, []).append(e)
    for g, v in sorted(by_grp.items(), key=lambda x: -len(x[1])):
        if len(v) > 1:
            print(f"  【{g}】{len(v)} 条: "
                  + ", ".join(parse_spec(x['spec'])[0] for x in v))

    # 跨维度同实体
    print("\n## 跨维度同实体（owner 路由候选）")
    by_ck2 = {}
    for e in es:
        ck = e.get("ckey") or ckey_of(e["spec"], aliases)
        by_ck2.setdefault(ck, set()).add(e["dim"])
    cross = {k: v for k, v in by_ck2.items() if len(v) > 1}
    if cross:
        for k, v in cross.items():
            spec = next(e["spec"] for e in es
                        if (e.get("ckey") or ckey_of(e["spec"], aliases)) == k)
            print(f"  {spec} → 出现在 {sorted(v)}")
    else:
        print("  （无）")


def stats():
    import collections
    d = load()
    es = d["entries"]
    print(f"总条目: {len(es)}  updated_round={d.get('updated_round')}")
    print("按轮次:", dict(collections.Counter(e["first_seen_round"] for e in es)))
    print("按维度:", dict(collections.Counter(e["dim"] for e in es)))
    print("按状态:", dict(collections.Counter(e["status"] for e in es)))
    print("有 ckey:", sum(1 for e in es if e.get("ckey")))
    print("needs_data:", sum(1 for e in es if e.get("needs_data")))
    print("有 parent:", sum(1 for e in es if e.get("parent")))


def backfill_ckey():
    """重算全部 ckey / group（幂等）。

    注意：归一化规则升级后（如 R4 加入编号前缀剥离）必须重跑，
    否则旧 ckey 会让 L2 层继续漏判。
    """
    d = load()
    aliases = load_aliases()
    changed = 0
    for e in d["entries"]:
        new_ck = ckey_of(e["spec"], aliases)
        if e.get("ckey") != new_ck:
            e["ckey"] = new_ck
            changed += 1
        g = group_of(_strip_code_prefix(parse_spec(e["spec"])[0]))
        if g:
            e["group"] = g
    save(d)
    print(f"recomputed ckey for {len(d['entries'])} entries（{changed} 条变化）")


def main():
    ap = argparse.ArgumentParser(prog="dedup2.py")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("check"); p.add_argument("spec")
    p = sub.add_parser("add")
    p.add_argument("spec")
    p.add_argument("--dim", required=True)
    p.add_argument("--round", type=int, required=True, dest="round_n")
    p.add_argument("--note", default="")
    p.add_argument("--rice", type=int, default=None)
    p.add_argument("--needs-data", action="store_true")
    p.add_argument("--parent", default="")
    p.add_argument("--cross-dim", default=None)
    p.add_argument("--force", action="store_true")
    sub.add_parser("audit")
    sub.add_parser("stats")
    sub.add_parser("backfill")

    a = ap.parse_args()
    if a.cmd == "check":
        check(a.spec)
    elif a.cmd == "add":
        cd = a.cross_dim.split(",") if a.cross_dim else None
        add(a.spec, a.dim, a.round_n, a.note, a.rice, a.needs_data,
            a.parent, cd, a.force)
    elif a.cmd == "audit":
        audit()
    elif a.cmd == "stats":
        stats()
    elif a.cmd == "backfill":
        backfill_ckey()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
