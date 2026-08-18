---
name: market-intel-agent
description: |
  德胧市场情报「动态中枢」元 Agent —— 把 10 个硬编码维度变成可路由的能力，
  按自然语言查询动态选维度、按需检索，而非每天固定全量扫描。
  当任何人 @它 问市场相关（竞品/客源/协议/会员/餐饮/会展/TMC/宏观），或说
  "查一下/市场情报/最近有什么机会/竞品在搞什么" 时激活。酒店专属数据外置到 hotel_config.yaml。
agent_created: true
version: 0.1.0-dynamic
tags_zh:
  - 德胧
  - 市场情报
  - 动态Agent
  - 元Agent
  - 能力路由
  - 去硬编码
  - AI Native
---

# MARKET-INTEL-AGENT · 动态市场情报中枢（元 Agent）

## 它解决什么（为什么不再是「死的」）

旧 10 个 `market-*-wb` skill 的问题：
- 每个都**硬编码**瑞湾的群 ID / 客户清单 / 对接人 / 地理坐标 / Bitable token；
- 触发靠**每天 6:30 固定全量跑 10 维**，不管有没有新情况；
- 维度固化、格式固化，只能在「市场扫描」孤岛场景运行。

本 Agent 的做法：
- **能力注册**：10 维抽象成 `capabilities.json`（维度/关键词/检索模板），不绑任何酒店；
- **酒店数据外置**：群 ID / 地理 / 对接人 / Bitable 全在 `hotel_config.yaml`，换酒店只改配置；
- **对话驱动路由**：接收自然语言 → `router.py` 动态选相关维度 → 只跑需要的，省 token、更聚焦；
- **融入所有场景**：可对话触发（飞书 @）、可嵌入销售/会员/餐饮工作流，不再是定时孤岛。

## 触发词

```
市场情报  查一下  竞品  客源  协议  会员  餐饮  会展  TMC  宏观  培训住宿
最近有什么机会  政策红利  促销  复购
```

## 运行流程

### Step 0 · 读取配置
加载 `hotel_config.yaml`（酒店名/地理/群 ID/对接人/Bitable/本地沉淀池路径）。
配置缺失不致命，用占位继续并提示补齐。

### Step 1 · 意图路由（动态核心）
调用 `router.py`：
```bash
python3 router.py "最近天津央企有什么培训住宿机会" --geo "天津滨海"
# → mode=targeted, 选中 [企业协议(two), TMC订单(tmc)]
python3 router.py "国能、天津港培训机会" --use-llm --json
# → LLM 语义路由：命中 [企业协议(two), TMC订单(tmc)]（关键词法只能落 full）
```
- 规则匹配 10 维关键词（默认、零依赖）；
- **真实 LLM 语义路由**（`--use-llm`）：调用 OpenAI 兼容端点（本地 LiteLLM router 或任意兼容服务，环境变量 `MARKET_LLM_BASE_URL/MODEL/API_KEY` 覆盖），理解"国企培训"→ tmc+企业协议 这类隐含意图；网络/模型/解析任一异常**自动降级**关键词路由，绝不致命。
未命中 → 保底全量或追问澄清。

### Step 2 · 执行闭环（触发即产出真实情报）
`feishu_receiver.py` 命中 `@bot` 后默认调用 `agent_loop.py` 直接产出，不再只回"执行指令"：
```bash
python3 agent_loop.py "最近天津央企有什么培训住宿机会" --use-llm
```
`agent_loop` 对每个选中维度：
1. **拉方法论**：读 `skills/_generated/market-<dim>-wb/SKILL.md`（维度专属，init 生成）；
2. **拉内部沉淀**：`signal_registry.json` 同维条目 + `runtime/harvest/<dim>/` 历史资料；
3. **LLM 合成**：产出「①市场机会 ②可跟进客户/渠道 ③行动建议 ④需外部检索」四段简报。
- 内部无数据时**诚实标注「⚠️需外部检索」**，绝不编造事实；
- LLM 不可用时降级为方法论摘要模板，不崩溃。
> 真实**外部 Web 检索**由 WorkBuddy 侧维度 skill（自带 WebSearch）或运营者执行，本脚本不内嵌抓网页。

### Step 3 · 整合交付
- 对话内直接返回结构化情报（维度分组 + 简报 + 闭环动作）；
- 或推飞书专群（读 `hotel_config.yaml` 的 feishu_chat_<dim>）；
- 重大变化可主动推送（见下方事件触发）。

## 主动/事件触发
- **对话触发（飞书 @）✅ 已实现**：`feishu_receiver.py` 轮询维度专群，检测 `@bot` → 路由 → **执行闭环产出情报**。
  默认 **dry-run**（不发，只打印拟回复）；`--send` 才真正发。自动排除 bot 自身消息（防回环）、
  增量去重（`receiver_state.json`）、首跑只建基线不回复历史积压。`--self-test` 可离线验证整条链路。
- **事件触发 ✅ 已实现**：`event_watch.py` 按监控清单（默认每维一条，或 `watchlist.yaml` 自定义）主动巡检各维度，
  LLM 判【新增】/【无新增】，仅当与上次不同（state 防刷屏）才推对应专群。
  `event_watch.py --send` 推新增；`--interval 3600` 常驻每小时巡检。默认 dry-run 安全。
- **场景插件**：销售写方案时调用"竞品在搞什么促销"→ 即时拉数字渠道+餐饮情报（`agent_loop` 直出）。

## 分发与自包含
本技能目录自带 `skills/_generated/`（10 维占位技能），**可脱离整个仓库独立安装使用**：
把 `~/.workbuddy/skills/market-intel-agent/` 整目录复制给对方即可。整库 clone 时 `init.py` 会重新生成
顶层 `skills/_generated/`（酒店专属），`agent_loop` 优先用重新生成的版本。

## 与旧 10 skill 的关系
本 Agent 是**调度中枢**，旧 10 个 skill 的检索逻辑可逐步下沉为「能力实现」被调用。
迁移路径：先平行使用（本 Agent 做路由，旧 skill 做执行），验证稳定后旧 skill 退化为能力库。

## 红线（沿用）
- 数据真实：来源实时查或权威源，失败明示，不编造；
- 地理锚定：只用 hotel_config 里的 geo，不编造其他城市；
- 不自动发飞书/Bitable 除非用户授权（publish_signals 单独步骤）。

## 验证
```bash
python3 test_router.py   # 8/8 场景路由通过
```
