---
name: market-iteration-toolkit
description: 德胧市场侧 10 维度小分子 Agent 联邦的「活体迭代环」工具链。用于跑新一轮扫描（R_n）、跨轮去重、质量门裁决、沉淀检索、检索扩展，并复盘全过程。当说"跑一轮市场扫描/迭代/去重/沉淀/检索扩展/复盘"时使用。
agent_created: true
---

# 德胧市场侧小分子 Agent 联邦 · 活体迭代环工具链

## 何时用

- 用户要"跑一轮新的市场信息 / 迭代 10 个市场 Agent / 复盘全过程"
- 用户提到"去重 / 沉淀 / 检索扩展 / 质量门 / 信号注册表"
- 要做跨轮信号去重、把新信号写回各维度 SKILL、推送飞书/Bitable

## 架构：活体迭代环 5 阶段

1. **扫描**：派 N 个 sub-agent 双跑（内层德胧资产 harvest + 外层 WebSearch 实时检索），每维产出 `scan_roundN_<dim>.md` + `signals_roundN_<dim>.txt`
2. **跨轮去重**：`dedup2.py` 四层引擎，扫描前后都跑审计
3. **质量门裁决**：`quality_linter.py` 机器 linter（v3 = 11 维）
4. **升级记忆**：更新 `signal_registry.json`（单一真相源）
5. **回写沉淀**：写回各维 SKILL「三-B 沉淀资产库」+ 飞书/Bitable 推送（**须用户确认**）

## 核心文件位置（德胧项目）

工具脚本在迭代工作区：
`/Users/ccc/WorkBuddy/market-seven-mice/_iteration/`
- `dedup2.py` — 去重引擎 v2（L1 sha1 / L2 规范化别名 / L3 bigram Jaccard / L4 集团父子）
- `harvest_index.py` — 沉淀索引（SQLite FTS5 中文 bigram，5216 条）
- `query_expander.py` — 检索扩展器（候选矩阵，每维 80-100+ vs 硬编码 3）
- `quality_linter.py` — 质量门 linter v3（11 维，输出 rN_gate.json）
- `backfill_registry.py` — **每轮必跑**：把 signals_rN_*.txt 回填进 signal_registry.json（内部去重基线）
- `signal_registry.json` — 信号注册表（单一真相源 / 去重基线）
- `scan_roundN_<dim>.md` / `signals_roundN_<dim>.txt` — 各轮产出
- `R4_SCAN_BRIEF.md` — 作战 brief 模板（地理约束/双层双跑/去重基线/质量门/红线）
- `ITERATION_RUNBOOK.md` / `QUALITY_GATE_SPEC.md` / `AGENT_FEDERATION.md` — 治理文档

## 标准操作（一轮 R_N）

```bash
cd /Users/ccc/WorkBuddy/market-seven-mice/_iteration
PY=/Users/ccc/.workbuddy/binaries/python/versions/3.13.12/bin/python3

# 0. 写 R{N}_SCAN_BRIEF.md（地理锚点=天津瑞湾开元名都 39.000983,117.709983；红线：不自动发飞书/Bitable）
# 1. 派 sub-agent 扫描 9 维（餐饮四 PAUSED），双跑内层+外层，产出 scan_roundN_*.md + signals_roundN_*.txt

# 2. 去重审计（扫描前后都跑）
$PY dedup2.py audit          # 看 L2 确定重复 / L3 模糊 / L4 集团簇 / 跨维
$PY dedup2.py check "中交一航局|滨海|account"   # 单条检查 SEEN/FUZZY/NEW

# 3. 检索扩展（给 sub-agent 生成本轮候选查询，避开历史）
$PY query_expander.py two --limit 12
$PY query_expander.py two --record "跑过的查询1,跑过的查询2"   # 跑完记录，下轮避开

# 4. 沉淀检索（内层查证）
$PY harvest_index.py search "船员外派" --dim five
$PY harvest_index.py stats

# 5. 质量门 linter v3 —— 必须先用「不含 R(N) 的 R(N-1) 基线」跑，否则 R(N) 信号自匹配成假重复！
#   做法：临时把注册表切成 first_seen_round != N，跑 linter，再恢复（见下方「linter 基线陷阱」）
$PY quality_linter.py --round N --json rN_gate.json
#   → 聚合：每维 11 门 P/A/S；93 PASS / 6 WARN / 0 FAIL（R5 实测）

# 6. 升级注册表（每轮必跑，内部去重基线，≠ 对外发布！）
$PY backfill_registry.py          # 解析 signals_r{N}_*.txt，dedup2.add 批量回填（SEEN 跳过/FUZZY 拦截）
#   → 跑完 R(N) 信号进注册表，供 R(N+1) 去重基线。ckey 随别名更新自动重算。
```

## linter 基线陷阱（R5 踩过的坑）

`quality_linter.py` 的 ④去重门调 `dedup2.py check`，读**当前** signal_registry.json。
若你先 `backfill_registry.py` 把 R(N) 信号写进注册表再跑 linter，linter 会把 R(N) 信号和注册表里「它自己」匹配成 `SEEN(L1-exact)` 假重复。

**正确顺序**：
1. sub-agent 扫描产 signals_rN_*.txt
2. 用**不含 R(N) 的基线**跑 linter（临时 `entries = [e for e in d['entries'] if e['first_seen_round']!=N]`，跑完恢复）
3. **再** backfill_registry.py 回填 R(N)
4. audit 验证 R(N) 对 R(N-1) 零碰撞

## 注册表写回 ≠ 对外发布（纪律澄清）

- **④ 升级记忆（写注册表）**：内部去重基线，每轮必做，**不问用户授权**。漏写会导致下轮把旧信号当新重报。
- **⑤ 回写沉淀（SKILL 三-B + 飞书 + Bitable）**：对外发布，**须用户明确授权**（红线条）。
- R4 曾因未授权发布而只写了 23/67 注册表，R5 才靠 backfill_registry.py 补回 —— 此坑已固化为上面两步顺序。

## 去重 v2 关键 API（dedup2.py）

- `check(spec)` → `SEEN` / `FUZZY` / `NEW` / `NEW_SAME_GROUP`（L1→L2→L3→L4）
- `add(spec, --rice --needs-data --parent --cross-dim --force)`
- `audit` → 重复簇 / 集团簇 / 跨维同实体
- `backfill` → 全量重算 ckey（新增别名/规则后必须跑）
- 已知坑：编号前缀（"A8·"）干扰 ckey → 已加 `_strip_code_prefix`；AND 语义 0 命中 → 词单位+OR 降级

## 质量门 v3（11 维）

①来源标注 ②真实性 ③地理相关性 ④去重 ⑤可执行性 ⑥双层双跑 ⑦权威源置信度分级 ⑧跨轮一致性 ⑨执行量化 ⑩RICE完整 ⑪回写对账
- FAIL = 不发送；WARN = 降级发送（须标注）；全 PASS = SEND-ELIGIBLE
- linter 输出 JSON：`checks` 是 dict（门名→"PASS"/"WARN"/"FAIL"），聚合按全字符串匹配（勿按字符切分！）

## 红线（纪律）

- **AI 不自动发布**：飞书发送 / Bitable 写入 / SKILL 三-B 写回 须用户明确授权。每轮只到草稿。
- **但注册表写回（④）是内部基线，每轮必做，不问授权**（见「注册表写回 ≠ 对外发布」）。
- 诚实标注 OPEN / PAUSED / BLOCKED，不编造「已解决」。
- 地理：津南国展 50km 外非主场须降级。
- RICE 量纲须统一（推荐 R×I×C×E 乘积），禁止跨轮混用求和/乘积尺度。
- 诚实标注 OPEN / PAUSED / BLOCKED，不编造"已解决"。
- 地理：津南国展 50km 外非主场须降级。

## 复盘的产出

- `ITERATION_REVIEW_R{N-1}_R{N}.md`：成果表 + 全链路 + 三大能力迭代评估 + sub-agent 反馈闭环 + 未闭环项 + 建议 + 发布纪律
- 三大能力（去重/沉淀/检索扩展）每轮应在实战中暴露缺陷并当场修，形成闭环
