# 部署详细指南 · 两种路径搭建 + 历史资料上传

> 配套 README.md 的「⚠️ 前置资料清单」。本文给出每一步的具体操作，
> 重点讲清**历史资料上传的两条路径**如何搭建。

---

## 0. 先决条件

- 电脑/服务器装了 Python 3.11+（运行时脚本用）
- 路径 B 额外需要：飞书开放平台账号 + 能建自建应用

---

## 1. 前置资料清单（再次强调，这是激活精准情报的底）

| 资料 | 放哪 | 格式要求 |
|---|---|---|
| 历史协议单位清单 | `runtime/harvest/protocol_units.txt` | 纯文本，每行一个单位名 |
| 客源渠道台账 | `runtime/harvest/source_channels.csv` | 列：渠道,占比,来源地 |
| 餐饮/宴会/会议订单 | `runtime/harvest/fnb_orders.csv` 等 | 列：日期,类型,金额,客户 |
| 飞书市场专群历史 | 路径 B 用 `harvest_chat.py` 拉取 | 需 bot 在群内 |
| 酒店基础信息 | `hotel_config.yaml` | 见 examples/ |

**资料越全 → 去重越准、迭代核验越强、情报越精准。** 只交 P0 也能跑，但维度激活不全。

---

## 2. 路径 A：纯 WorkBuddy 独立 Agent（无需飞书）

适合：先验证、个人使用、离线环境。

### 2.1 安装与导入
```bash
git clone <repo> && cd delonix-market-federation
# 先跑 init.py 生成你酒店的 10 个维度技能（见 §2.2），再复制：
cp -R skills/_generated/* ~/.workbuddy/skills/      # 你酒店专属 10 维（init 生成）
cp -R skills/market-intel-agent ~/.workbuddy/skills/   # 动态路由元 Agent
cp -R skills/market-iteration-toolkit ~/.workbuddy/skills/  # 迭代工具包
# 注：skills/_ref_ruiwan/ 是瑞湾满血参考样例，仅供借鉴补全，勿直接部署
```

### 2.2 初始化
```bash
python3 init.py --hotel "XX酒店" --geo "城市/区" --mode A
```
`init.py` 会一次性完成：
1. 生成 `hotel_config.yaml`（酒店名/地理/空 chat 映射）
2. 重置 `runtime/signal_registry.json` 为空（不带瑞湾数据）
3. 建 `runtime/harvest/` 目录 + 放好资料占位文件
4. **调用 `gen_dim_skills.py` 生成 `skills/_generated/` 下 10 个你酒店专属维度技能**
5. 按 mode 输出后续步骤提示

### 2.3 放资料
按 §1 表格把文件放进 `runtime/harvest/`。

### 2.4 触发使用
在 WorkBuddy 对话里直接提问，市场情报中枢会自动路由维度：
```
@market-intel-agent 最近本地有什么央企培训住宿机会？
@market-intel-agent 竞品在搞什么促销？
```
返回的是**按维度分组**的情报（新发现 + 迭代核验），不是固定全量。

### 2.5 路径 A 的局限
- 历史沉淀只能靠你上传的 Excel/文本（没有飞书群自动拉取）
- 发布只在对话内，不推群

---

## 3. 路径 B：飞书环境交互 Agent（团队协同）

适合：团队日常、销售/会员/餐饮随时 @问、情报推群可协作。

### 3.1 飞书自建应用
1. 飞书开放平台建应用，拿到 App ID / Secret
2. 开通权限：`im:message`、`im:message:readonly`、`contact:user`（按需）
3. 装 `lark-cli`，配置 App ID/Secret 到 `~/.lark-cli/config.json`
4. **把 bot 拉进该酒店的市场专群**（每个维度一个群，或共用群改 dim 映射）

### 3.2 初始化（mode B）
```bash
python3 init.py --hotel "XX酒店" --geo "城市/区" --mode B \
  --chat-tmc oc_xxx --chat-two oc_yyy --chat-seven oc_zzz
# 其余维度专群 chat_id 在生成的 hotel_config.yaml 里补齐（feishu_chat_<dim>）
```
`init.py` 同样会生成 `skills/_generated/` 的 10 个维度技能，并把你填的 chat_id 注入进去。

### 3.3 拉取飞书群历史（内层查证主源）
```bash
cd runtime
python3 harvest_chat.py --chat-id <群ID> --out harvest/<dim>.json
python3 harvest_index.py build        # 建 FTS5 索引
```
这一步把飞书群沉淀的协议单位、客源渠道、成交经验变成可检索内层资产。

### 3.4 触发使用
在飞书市场专群 `@bot 最近有什么央企培训机会`，bot 调用 `market-intel-agent` 路由 + 检索，回推结构化情报卡片。
也可配置定时（保留原 6:30 全量保底）+ 对话触发并存。

### 3.5 路径 B 的注意
- bot 不在群里 → 发布失败（瑞湾 R23 踩过：3/10 维失败）。务必先验证 `lark-cli whoami --as bot` + 发测试消息
- Bitable 写权需 bot 自建 base 或集团分配（瑞湾 91403 权限坑）

---

## 4. 一键初始化脚本说明（init.py）

`init.py` 做五件事：
1. 生成 `hotel_config.yaml`（从 examples 模板，填入酒店名/地理/mode）
2. 重置 `runtime/signal_registry.json` 为空（不带瑞湾数据）
3. 建 `runtime/harvest/` 目录 + 放好资料占位文件
4. **调用 `gen_dim_skills.py` 生成 `skills/_generated/` 下 10 个你酒店专属维度技能**（模板+配置注入，方法论通用、数据外置）
5. 按 mode 输出后续步骤提示（路径 A / B 不同）

> **运行时配置外置**：所有 runtime 脚本（publish_signals / harvest / dedup 等）统一经 `runtime/runtime_config.py` 读取 `hotel_config.yaml`——飞书 bin、Bitable token、各维度专群 chat_id、本地路径全部配置化，**脚本零硬编码**，换酒店只需换一份 yaml。

```bash
python3 init.py --help
```

---

## 5. 激活更多维度的对应关系

交了哪些资料，哪些维度就被激活：

| 你交的资料 | 激活的维度 |
|---|---|
| 协议单位清单 | 企业协议(二) / TMC(十) / 潜在客源(八) |
| 飞书群历史（含销售跟进） | 全部 10 维（内层查证） |
| 客源渠道台账 | 潜在客源(八) / 数字渠道(六) |
| 餐饮/宴会订单 | 餐饮宴会(四) |
| 会议/展会记录 | 会议会展(七) |
| 会员数据 | 会员增购(三) |
| 长住/外派记录 | 长住公寓(五) |
| 宏观/政策剪报 | 潜在广域(九) / 休闲度假(一) |

**结论：P0（协议单位+飞书群+酒店信息）交齐，10 维全部有内层源；P1/P2 让每个维度的情报更厚、更准。**

---

## 6. 验证

```bash
# 动态路由（不依赖外部）
cd skills/market-intel-agent && python3 test_router.py   # 8/8

# 路径 A
WorkBuddy 对话 @market-intel-agent "最近央企培训机会" → 返回分维度情报

# 路径 B
飞书群 @bot "竞品促销" → 返回卡片并推群
```
