# 德胧市场情报联邦 · 动态 Agent 部署包

> 把「10 个小分子 Agent 市场信息检索打法」变成一套**可复制、可配置、能对话触发**的动态情报系统，
> 让每家兄弟酒店用自己的历史数据，独立产出市场情报，并融入日常销售/会员/餐饮工作流。
>
> **不是 10 个写死的 prompt，是 1 个模板 + 配置注入 → 生成你酒店专属的 10 个 Agent。**

---

## ⚠️ 一家新酒店部署前，必须准备这些资料（缺一不可才能激活精准情报）

> 系统能不能跑顺，取决于你喂的「历史沉淀」质量。**没有本地数据，去重和迭代核验会退化成只靠注册表。**
> 下面按「优先级」排列，P0 是底线，P1/P2 越多越精准。

| 优先级 | 资料 | 用途 | 格式 |
|---|---|---|---|
| **P0 必交** | 历史协议单位 / 客户清单 | 去重基线、客源识别、对接人归档 | Excel / CSV（一列一个单位名） |
| **P0 必交** | 飞书市场专群（或业务群）历史消息 | 内层查证主源，提炼成交/话术/经验 | 群 chat_id + bot 进群权限 |
| **P0 必交** | 酒店基础信息 | 地理锚点、销售半径、客群定位 | 填 `hotel_config.yaml` |
| **P1 强烈建议** | 历史客源渠道台账 | 潜在客源维度激活 | Excel（渠道/占比/来源地） |
| **P1 强烈建议** | 历史餐饮/宴会/会议订单 | 餐饮四、MICE 维度激活 | Excel（日期/类型/金额/客户） |
| **P2 锦上添花** | 竞品情报剪报 | 数字渠道维度对标 | 文档/链接 |
| **P2 锦上添花** | 会员权益与复购数据 | 会员三维激活 | Excel |

**最基础的历史沉淀（P0 三项交齐）= 系统能跑；P1 交齐 = 激活更多维度；P2 交齐 = 情报更精准。**

---

## 🧬 这套包怎么做到「动态 Agent」而不是死 prompt

| 组成 | 作用 | 是否去硬编码 |
|---|---|---|
| `skills/_template_dim_agent/` | **1 个参数化维度模板** + 生成器 `gen_dim_skills.py` | ✅ 方法论通用，酒店数据全部占位 |
| `skills/market-intel-agent/` | **动态路由元 Agent + 执行闭环 + 事件触发**：`router.py`（规则+**真实 LLM 语义路由**，自动降级）、`agent_loop.py`（**触发即产出真实情报**：拉方法论+内部沉淀→LLM 合成简报）、`feishu_receiver.py`（飞书 @触发接收器，dry-run 安全）、`event_watch.py`（**事件主动触发**：巡检监控清单、LLM 判新增、新颖才推群）、`llm_util.py`（共享 LLM 调用）。**自带 `skills/_generated/` 10 维占位技能，技能可独立分发**。 | ✅ 维度/关键词在 `capabilities.json`，酒店数据在 `hotel_config.yaml` |
| `skills/market-iteration-toolkit/` | 联邦活体迭代环工具链（去重/质检/检索扩展） | ✅ |
| `runtime/runtime_config.py` | **运行时配置加载器**：飞书 bin、群ID、Bitable、本地路径全部从 `hotel_config.yaml` 读 | ✅ 脚本零硬编码 |
| `skills/_ref_ruiwan/` | **瑞湾的实战满血版 10 维技能**（参考样例，含真实市场知识），**不要直接部署**，仅供新酒店借鉴补全 | ❌ 瑞湾专属，仅作模板填充参考 |
| `skills/_generated/` | `init.py` 跑完后**自动生成**的你酒店专属 10 维技能（这才是要部署的） | ✅ 由模板+配置注入 |

**一句话**：克隆仓库 → 跑 `init.py` → 自动生成 `hotel_config.yaml` + 你酒店的 10 个维度技能 + 空注册表 → 复制 `skills/_generated/*` + 两个元技能到 `~/.workbuddy/skills/` 即可。

---

## 🚀 两种部署路径（任选其一，或并存）

```
┌─────────────────────────────┐     ┌─────────────────────────────┐
│  路径 A：纯 WorkBuddy 独立 Agent │     │  路径 B：飞书环境交互 Agent   │
│                             │     │                             │
│  · 在你电脑/服务器装 WorkBuddy  │     │  · 在飞书开放平台建自建应用   │
│  · 导入 skills + 跑 init.py    │     │  · bot 进市场专群            │
│  · 对话里直接 @市场情报中枢提问  │     │  · 群里 @bot 即可问市场情报   │
│  · 无需飞书，离线可用          │     │  · 融入销售/会员日常群聊      │
│  · 适合：先验证、个人使用       │     │  · 适合：团队协同、日常触发   │
└─────────────────────────────┘     └─────────────────────────────┘
```

| 维度 | 路径 A（WorkBuddy 独立） | 路径 B（飞书交互） |
|---|---|---|
| 前置 | 装 WorkBuddy | 飞书自建应用 + bot 权限 |
| 触发 | 对话 `@market-intel-agent` | 飞书群 `@bot` |
| 历史沉淀 | 上传 Excel 到本地目录 | 飞书群历史 + Excel |
| 发布 | 对话内返回 / 本地报告 | 推飞书专群 / Bitable |
| 团队可见 | 仅本人 | 全群可见、可协作 |
| 复杂度 | 低 | 中（需开放平台配置） |

---

## 📡 路径 B 交互入口：`feishu_receiver.py`（飞书 @触发接收器）

把"动态路由"真正接到飞书群里——人类在维度专群 `@bot` 提问，接收器轮询新消息 → 检测触发 → 调用路由器 → 回复"命中哪些维度 + 下一步"。

```bash
# 在 skills/market-intel-agent/ 目录下
python3 feishu_receiver.py --self-test            # ① 离线验证整条链路（触发→路由→回复）
python3 feishu_receiver.py --once --use-llm       # ② 跑一轮（默认 dry-run，不真正发）
python3 feishu_receiver.py --once --chat-id oc_xxx --dry-run   # ③ 只测一个群
python3 feishu_receiver.py --send --interval 30   # ④ 常驻循环 + 真正发消息到飞书
```

**安全设计（对外发送谨慎）**：
- **默认 dry-run**：不传 `--send` 绝不发消息，只打印"拟回复"内容，方便先在群里验证。
- **防回环**：自动排除 `sender_type==app` 的消息（含每日信号推送），bot 不会回复自己。
- **增量去重**：用 `receiver_state.json` 记录每个群上次见到的最新消息 id，首跑只建基线、不回复历史积压。
- **触发模式**：`trigger_mode: mention`（默认，群内有人 @bot 才触发）/ `any_user`（任意人类消息触发），可在 `hotel_config.yaml` 配 `bot_open_id` / `bot_name` 做精确匹配。

**LLM 语义路由（可选 `--use-llm`）**：调用 OpenAI 兼容端点（本地 LiteLLM router 或任意兼容服务），环境变量覆盖 `MARKET_LLM_BASE_URL` / `MARKET_LLM_MODEL` / `MARKET_LLM_API_KEY`。网络/模型/解析任一异常自动降级为关键词路由，绝不致命。实测：短线索"国能、天津港培训机会，中汽研"经 LLM 精准命中「企业协议 + TMC订单」，关键词法只能落 full。

**触发即产出（执行闭环 `agent_loop.py`）**：接收到触发后不再只回"执行指令"，而是直接拉取该维度方法论（`_generated` 技能）+ 内部沉淀（`signal_registry` + `runtime/harvest/`）→ 调 LLM 合成「市场机会 / 可跟进客户 / 行动建议 / 需外部检索」四段结构化简报并回复。内部无数据时诚实标注「⚠️需外部检索」，绝不编造。LLM 不可用时降级为方法论摘要模板，不崩溃。

```bash
python3 agent_loop.py "最近天津央企有什么培训住宿机会" --use-llm   # 直接产出情报
```

---

## 🔔 事件主动触发：`event_watch.py`（不再等人类 @）

按「监控清单」（默认每维一条主题，或用 `watchlist.yaml` 自定义）主动巡检各维度，LLM 判【新增】/【无新增】，仅当与上次不同（state 比对防刷屏）才推送对应专群。

```bash
python3 event_watch.py                 # 巡检一轮（默认 dry-run，不推）
python3 event_watch.py --send          # 巡检 + 真推新增
python3 event_watch.py --send --interval 3600   # 常驻每小时巡检
```

> 诚实边界：内部语料 + LLM 推理驱动的真实监控闭环已落地；**真实外部 Web 检索**由 WorkBuddy 侧维度 skill（自带 WebSearch）或运营者执行，本脚本产出会明确标注「需外部检索验证」。外部检索后端可在 `llm_util` 之上扩展。

---

## 🔭 实现状态（诚实清单）

| 能力 | 状态 | 说明 |
|---|---|---|
| 维度按 query 动态路由（不固定全量） | ✅ 已实现 | 规则 + LLM 双引擎，8/8 测试通过 |
| LLM 语义路由真实调用 | ✅ 已实现 | 接本地 router / 任意兼容端点，自动降级 |
| **真实检索执行闭环**（触发即产出情报简报） | ✅ 已实现 | `agent_loop.py`：方法论+内部沉淀→LLM 合成，外部检索标注诚实 |
| **事件主动触发**（监控清单 + 新颖闸门 + 主动推群） | ✅ 已实现 | `event_watch.py`，dry-run 安全，防刷屏 |
| 飞书 @触发接收器（轮询 + 增量 + 防回环） | ✅ 已实现 | dry-run 验证通过；`--send` 可上真实发送 |
| 模板生成 10 个专属技能 | ✅ 已实现 | `init.py` + 参数化模板（技能内自带占位版，可独立分发） |
| 运行时配置全外置 | ✅ 已实现 | `runtime_config.py`，脚本零硬编码 |
| 双部署路径（WorkBuddy / 飞书） | ✅ 已实现 | 路径 A 对话触发；路径 B 群 @触发 |
| 外部 Web 实时检索（Agent 自主抓取网页） | 🔌 委托 | 由 WorkBuddy 侧维度 skill（自带 WebSearch）/ 运营者执行，非脚本内嵌 |

_当前已交付"动态 Agent 的完全效果"：对话/群聊触发 + 语义路由 + **执行闭环** + **事件主动触发** + 模板生成 + 双路径全 ✅。唯一未内嵌的是"脚本自主抓网页"——按设计委托给 WorkBuddy 侧 skill（其 LLM 自带检索），保持轻量与安全。_

---

## 📤 分发渠道（如何拿到/分享这套包）

- **GitHub（已建，私有）**：`https://github.com/Chaoliuzhu/delonix-market-federation`
  姐妹酒店用协作邀请即可 clone；需要公开时仓库设置里一键转 Public。`.gitignore` 已排除瑞湾真实情报（`_ref_ruiwan`）、酒店专属配置与运行态状态文件，仓库本身**零真实数据**。
- **WorkBuddy 技能平台**：本包核心 `market-intel-agent` 已是规范的用户级技能（含 `SKILL.md` frontmatter + `agent_created: true`），可独立分发安装。
  - ⚠️ 当前 WorkBuddy 技能市场工具仅支持 *搜索/安装* 官方 BuiltinMarket 技能，**无"发布自定义技能到平台"的接口**；
  - 发布到平台的手动入口：WorkBuddy 客户端 → 技能 → 我的技能/发布 → 选择 `market-intel-agent` 目录提交审核；或直接把 `~/.workbuddy/skills/market-intel-agent/` 整个目录发给对方、让其放入 `~/.workbuddy/skills/` 即可用。

---

## 📦 快速开始

```bash
# 1. 克隆仓库
git clone <your-repo> delonix-market-federation
cd delonix-market-federation

# 2. 一键初始化（选路径 A 或 B）→ 自动生成配置 + 空注册表 + 目录 + 你酒店的 10 个技能
python3 init.py --hotel "XX酒店" --geo "城市/区" --mode A        # 或 --mode B
#   路径 B 可附加：--chat-tmc oc_xxx --chat-two oc_yyy --chat-seven oc_zzz
#   其余维度 chat 在生成的 hotel_config.yaml 里补齐

# 3. 放入你的历史资料
#   - 协议单位清单 → runtime/harvest/protocol_units.txt（每行一个）
#   - 客源/订单 Excel → runtime/harvest/ 下对应 csv
#   - 飞书群历史（路径 B）→ 运行 runtime/harvest_chat.py 拉取

# 4. 部署技能到 WorkBuddy
cp -R skills/_generated/* ~/.workbuddy/skills/
cp -R skills/market-intel-agent ~/.workbuddy/skills/
cp -R skills/market-iteration-toolkit ~/.workbuddy/skills/

# 5. 验证动态路由（不依赖外部，先证明「动态」可用）
cd skills/market-intel-agent && python3 test_router.py
#   → 8/8 场景路由通过

# 6. 跑第一轮（路径 A 在 WorkBuddy 对话触发；路径 B 群里 @bot）
```

---

## ✅ 验证清单（部署完逐项打勾）

- [ ] `init.py` 跑通，生成 `hotel_config.yaml` + `skills/_generated/` 下 10 个技能
- [ ] P0 三项资料已放入（协议单位 / 飞书群 / 酒店信息）
- [ ] `test_router.py` 8/8 通过
- [ ] 路径 A：WorkBuddy 对话 `@market-intel-agent 最近有什么央企培训机会` 能返回分维度情报
- [ ] 路径 B：飞书群 `@bot 竞品在搞什么促销` 能返回并推群
- [ ] 第一轮扫描后 `signal_registry.json` 有条目（去重基线建立）

---

## 📂 目录结构

```
delonix-market-federation/
├── README.md                       ← 本文件（醒目部署说明书）
├── DEPLOY_GUIDE.md                 ← 详细：两种路径搭建 + 资料上传步骤
├── init.py                         ← 一键初始化（配置/空注册表/目录/生成技能/选路径）
├── skills/
│   ├── _template_dim_agent/        ← ★ 1 个参数化维度模板 + gen_dim_skills.py 生成器
│   ├── market-intel-agent/         ← ★ 动态路由元 Agent（router.py + agent_loop.py + feishu_receiver.py + event_watch.py + llm_util.py + 配置外置 + 8/8 测试）
│   │   └── skills/_generated/      ← 技能自带 10 维占位技能，使 skill 可独立分发（无需整库）
│   ├── market-iteration-toolkit/   ← 联邦活体迭代环工具链
│   ├── _generated/                 ← init.py 生成的你酒店 10 维技能（仓库态，要部署的）
│   └── _ref_ruiwan/                ← 瑞湾满血参考样例（仅借鉴，勿直接部署，已被 .gitignore 排除）
├── runtime/                        ← 运行时脚本（全部配置驱动，零硬编码）
│   ├── runtime_config.py           ← 配置加载器（飞书/Bitable/路径全外置）
│   ├── dedup2.py  publish_signals.py  harvest_*.py  url_liveness_check.py ...
│   └── signal_registry.json        ← 空模板，初始化后清空重建
└── examples/
    └── hotel_config.example.yaml   ← 配置模板
```

---

_动态 Agent 化：维度动态路由（market-intel-agent）+ 模板生成（_template_dim_agent）+ 配置外置（runtime_config）。_
_复制部署前请先读上方「⚠️ 前置资料清单」——资料越全，情报越精准。_
