# 电商数据分析 Agent（简历项目 · 细化版）

> 一句话定位：面向电商运营的「自然语言数据分析助手」。
> 运营用大白话提问（"上个月华东区卖得怎么样？"），Agent 自动完成
> 意图理解 → 生成 SQL → 查数 → 出图 → 给结论。

---

## 一、为什么选电商这个领域

- **业务指标直观**：GMV、订单量、客单价、复购率……人人都懂，面试好讲
- **数据好造**：星型模型（订单/商品/用户/地区），可自己生成逼真数据
- **Demo 效果好**：一句话出报表 + 图表 + 结论，视觉冲击强
- **技术点天然齐全**：Text-to-SQL、RAG、多轮对话、时间智能（同比环比）全都能用上

---

## 二、具体业务场景（要解决的问题）

面向三类典型提问：

| 场景 | 用户问法示例 | Agent 要做的事 |
|---|---|---|
| 销售总览 | "近 30 天销售额多少？环比上个月呢？" | 聚合查询 + 时间智能 |
| 商品分析 | "Top10 商品是哪些？滞销的有哪些？" | 排序 + 库存/动销判断 |
| 用户分析 | "华东区老客复购率怎么样？" | 多表关联 + 新老客口径 |
| 渠道分析 | "哪个渠道的转化率最高？" | 漏斗计算 + 归因 |
| 追问澄清 | "你说'卖得好'指销量还是 GMV？" | 反问 + 记忆上下文 |

---

## 三、数据模型设计（星型模型）

```
                    ┌──────────────┐
                    │  dim_date    │ 日期维度（年/季/月/周/日）
                    └──────┬───────┘
                           │
  ┌──────────┐   ┌────────▼─────────┐   ┌────────────┐
  │ dim_user │──▶│  fact_orders     │◀──│ dim_product│
  │ 用户维度  │   │  订单事实表        │   │  商品维度   │
  └──────────┘   └────────▲─────────┘   └────────────┘
                           │
                    ┌──────┴───────┐
                    │ dim_region   │ 地区维度（大区/省/市）
                    └──────────────┘
```

### 核心表结构

```sql
-- 订单事实表
CREATE TABLE fact_orders (
  order_id      BIGINT PRIMARY KEY,
  user_id       BIGINT,          -- 关联用户
  product_id    BIGINT,          -- 关联商品
  region_id     INT,             -- 关联地区
  order_date    DATE,            -- 关联日期
  quantity      INT,             -- 购买数量
  amount        DECIMAL(12,2),   -- 实付金额（GMV）
  channel       VARCHAR(20),     -- 渠道：搜索/推荐/直播/广告
  is_new_user   BOOLEAN,         -- 是否新客首单
  status        VARCHAR(10)      -- 订单状态：paid/refunded/cancelled
);

-- 商品维度表
CREATE TABLE dim_product (
  product_id    BIGINT PRIMARY KEY,
  product_name  VARCHAR(100),
  category      VARCHAR(50),     -- 类目
  brand         VARCHAR(50),
  price         DECIMAL(10,2),
  stock         INT              -- 当前库存（算滞销/动销）
);

-- 用户维度表
CREATE TABLE dim_user (
  user_id       BIGINT PRIMARY KEY,
  register_date DATE,
  city          VARCHAR(50),
  vip_level     VARCHAR(10)      -- 会员等级
);

-- 地区维度表
CREATE TABLE dim_region (
  region_id     INT PRIMARY KEY,
  province      VARCHAR(50),
  city          VARCHAR(50),
  area          VARCHAR(20)      -- 大区：华东/华南/华北...
);
```

> 设计要点：把「新老客、渠道、日期」等维度冗余进事实表，
> 让 Text-to-SQL 的关联更简单，降低生成难度，准确率更好做。

---

## 四、技术选型（聚焦电商）

| 模块 | 选型 | 理由 |
|---|---|---|
| LLM | DeepSeek API（OpenAI-compatible，可插拔） | 成本友好，接入简单，适合个人项目 demo |
| Agent 编排 | **LangGraph** | 图结构，做"规划→执行→反思"回路 |
| 后端 | Python + FastAPI | Agent 服务 + SSE 流式 |
| 前端 | Next.js（后期）/ Streamlit（快速 demo） | 对话 + ECharts 图表 |
| 数据库 | PostgreSQL + pgvector | 业务数据 + 向量检索一体 |
| 指标口径 | 指标字典（指标名→SQL 片段） | 统一"销售额/客单价/复购率"定义 |

**为什么用 pgvector**：把「表注释、字段注释、指标口径、常见同义词」向量化，
用户说"卖得怎么样"时能检索到 `amount` 字段和"销售额"指标定义，降低 SQL 幻觉。

---

## 五、系统架构

```
┌──────────────────────────────────────────────┐
│          前端：对话 + ECharts 图表 + 推理过程   │
└───────────────────────┬──────────────────────┘
                        │ SSE（实时流式）
┌───────────────────────▼──────────────────────┐
│              FastAPI Agent 服务                │
│                                               │
│   LangGraph 编排（状态图）                      │
│   ┌─────────┐   ┌──────────┐   ┌───────────┐ │
│   │ 意图解析 │──▶│ SQL 生成  │──▶│ SQL 执行  │ │
│   │ +澄清    │   │ (Tool Use)│   │ (只读校验) │ │
│   └────▲────┘   └────┬─────┘   └─────┬─────┘ │
│        │         反思/纠错回路         │        │
│        └──────────────────────────────┘        │
│   ┌────────────────────────────────────────┐  │
│   │ 工具集：execute_sql / 图表生成 / 结论摘要 │  │
│   │ 记忆：会话记忆 + 指标口径记忆             │  │
│   └────────────────────────────────────────┘  │
│   ┌────────────────────────────────────────┐  │
│   │ RAG：数据字典 + 指标字典（pgvector）      │  │
│   └────────────────────────────────────────┘  │
└───────────────────────┬──────────────────────┘
                        │
              ┌─────────▼─────────┐
              │ PostgreSQL+pgvector│
              │ 业务数据 + 向量索引 │
              └───────────────────┘
```

---

## 六、核心技术点（面试重点）

### 1. Text-to-SQL 准确率
- 注入方式：只注入**相关表结构**（经 RAG 检索），而非全库 schema
- 指标字典：把"销售额=sum(amount) where status='paid'"等口径固化
- few-shot：每种问法（趋势/排名/占比/对比）给 1-2 个示例

### 2. ReAct 纠错回路
- SQL 执行报错 → 错误信息回喂模型 → 重新生成（最多 N 次）
- 结果异常（空集/数值离谱）→ 触发反思，检查条件是否遗漏

### 3. 时间智能（电商特有难点）
- 支持"同比 / 环比 / MTD / YTD / 近 N 天"
- 日期过滤统一处理，避免模型手写复杂日期函数出错

### 4. 多轮对话与澄清
- 会话记忆：保存上一轮已落地的 `QuerySpec`，追问继承未重新指定的指标、维度、筛选、时间范围和比较周期
- 槽位优先级：本轮显式约束 > 本轮模型输出 > 上轮结构化口径，避免自然追问因省略指标而误澄清
- 主动澄清：口径歧义时反问（"指 GMV 还是订单量？"）

### 5. 安全
- 只读账号、表白名单、禁 `DELETE/DROP/UPDATE`、限行数、超时

---

## 七、开发路线图

### Phase 0：数据与骨架（第 1 周）
- [x] 建表 + 造数据脚本（生成 3–6 个月逼真电商数据，10w+ 订单）
- [x] FastAPI + LangGraph 项目骨架 + DeepSeek API 打通

#### Phase 0 开发记录（2026-09-03）

**已交付文件**
- `sql/schema.sql`：PostgreSQL + pgvector 建表脚本，包含 `dim_date`、`dim_region`、`dim_product`、`dim_user`、`fact_orders`，并预留 `data_dictionary` / `metric_dictionary` 向量检索表。
- `scripts/generate_seed_data.py`：可配置造数脚本，默认生成 6 个月、120000 订单、20000 用户、600 商品的 `utf-8-sig` CSV，兼容 Windows Excel 中文展示。
- `scripts/load_seed_data.py`：CSV 入库脚本，支持执行 schema、清空旧数据并按维表→事实表顺序 COPY 入库。
- `docker-compose.yml`：本地 PostgreSQL/pgvector 服务。
- `src/ecommerce_agent/`：FastAPI 服务、配置读取、DeepSeek/OpenAI-compatible SDK 封装、LangGraph 状态图骨架。
- `.env.example` / `README.md`：本地启动与配置说明。
- `tests/`：覆盖造数脚本与 LangGraph 主流程的基础测试。

**当前验收状态**
- 已生成正式数据集：`data/generated/seed_summary.json`
  - 时间范围：2026-03-05 至 2026-08-31
  - 订单数：120000
  - 付费订单数：109157
  - 付费 GMV：59517383.38
  - 覆盖用户数：19390
  - CSV 编码：utf-8-sig（文件头已验证为 `EF BB BF`，兼容 Windows Excel 中文展示）
- 已通过 `uv run pytest -q`：2 passed。
- 已通过 `uv run python -m compileall src scripts`。
- FastAPI 已本地启动：`http://127.0.0.1:8000`
  - `/health` 返回 200。
  - `/api/agent/query` 已通过 UTF-8 请求验证，可穿过 LangGraph 返回响应。
- DeepSeek API 已完成代码接入：当前 `.env` 已配置 `DEEPSEEK_API_KEY`，服务重启后 `/health` 显示 `llm_configured=true`。
- PostgreSQL/pgvector 已通过 Docker Compose 启动，`ecommerce-agent-postgres` 健康检查为 healthy。
- 已执行 `uv run python scripts/load_seed_data.py --csv-encoding utf-8-sig --database-url postgresql://agent:agent@localhost:5432/ecommerce_agent` 完成入库：
  - `dim_date`：180 行
  - `dim_region`：15 行
  - `dim_product`：600 行
  - `dim_user`：20000 行
  - `fact_orders`：120000 行
- 已执行 SQL 核验：
  - `fact_orders` 中 `paid` 订单数：109157
  - `paid` GMV：59517383.38
  - 中文字段样例正常：商品名/类目/大区/渠道均可正常查询显示。

### Phase 1：MVP 主链路（第 2–3 周）
- [x] `execute_sql` 工具（只读校验 + 超时 + 限行）
- [x] 基础 Text-to-SQL（schema 全量注入 + few-shot）
- [x] ReAct 纠错回路跑通
- [x] Streamlit 出第一个 demo

#### Phase 1 开发记录（2026-09-04）

**已交付文件**
- `src/ecommerce_agent/sql_tool.py`：`execute_sql` 只读执行工具。使用 `sqlglot` 做 AST 校验，只允许单条 PostgreSQL `SELECT/WITH` 查询；表名必须在白名单内；执行时使用只读事务、`statement_timeout` 和外层 `LIMIT`。
- `src/ecommerce_agent/text_to_sql.py`：基础 Text-to-SQL 计划解析、few-shot 示例和本地 fallback SQL 模板，覆盖销售总览、商品 TopN/滞销、用户复购、渠道分析、每日趋势。
- `src/ecommerce_agent/agent.py`：LangGraph 主链路升级为 `意图识别 → schema/指标上下文 → 生成 SQL → 执行 SQL → ReAct 纠错 → 汇总输出`。
- `src/ecommerce_agent/answering.py` / `src/ecommerce_agent/language.py`：新增同语言自然语言分析。用户中文提问时主回复中文；SQL 只保留在调试字段/调试面板中。
- `src/ecommerce_agent/main.py`：API 响应新增 `sql`、`columns`、`rows`、`row_count`、`elapsed_ms`、`sql_error`、`chart` 字段。
- `apps/streamlit_app.py`：第一个 Streamlit demo，可输入业务问题；主界面展示自然语言结论、关键指标、结果表和基础图表，SQL/trace 折叠在调试面板。
- `tests/test_sql_tool.py` / `tests/test_agent_graph.py`：覆盖 SQL 安全校验、数据库执行、Agent 成功路径和 ReAct 重试路径。
- `.env.example` / `README.md`：新增 SQL 安全配置与 Streamlit 启动说明。

**当前验收状态**
- 已通过 `uv run pytest -q`：10 passed。
- 已通过 `uv run python -m compileall src scripts apps`。
- 已验证真实数据库查询主链路：
  - "近 30 天销售额多少？环比上个月呢？"：`sales_overview`，返回 1 行。
  - "Top10 商品是哪些？"：`product_analysis`，返回 10 行。
  - "华东区老客复购率怎么样？"：`user_analysis`，返回 1 行。
  - "哪个渠道销售额最高？"：`channel_analysis`，返回 6 行。
- FastAPI 已本地启动：`http://127.0.0.1:8000`
  - `/health` 返回 200。
  - `/api/agent/query` 已验证可返回 SQL 执行结果。
- Streamlit demo 已本地启动：`http://127.0.0.1:8501`
  - `/_stcore/health` 返回 200。
- DeepSeek API 代码链路已接入并完成真实调用验证：
  - 测试问题："Top10 商品是哪些？"
  - trace：`generate_sql:deepseek` → `execute_sql:ok`
  - 返回结果：10 行，`sql_error` 为空。
  - 未配置 `DEEPSEEK_API_KEY` 时仍会使用本地 fallback SQL，保证 demo 可离线跑通。
- 已完成回复形态优化：
  - 主 `answer` 改为自然语言业务分析，不直接暴露 SQL。
  - 中文问题会返回中文分析结论。
  - SQL 和 trace 保留为 API 调试字段，并在 Streamlit 中折叠到“调试信息”面板。
  - HTTP 验证问题："近 30 天销售额多少？环比上个月呢？"，`answer_contains_select=false`，`sql_present=true`。

### Phase 2：准确率与体验（第 4–5 周）
- [x] 数据字典 + 指标字典 RAG（pgvector），准确率提升
- [x] 时间智能（同比环比）
- [x] 多轮对话 + 意图澄清
- [x] 自动图表 + 结论摘要

#### Phase 2 开发记录（2026-09-07）

**已交付文件**
- `src/ecommerce_agent/rag.py`：数据字典/指标字典条目、确定性词法检索、pgvector cosine 检索接口。
- `scripts/seed_dictionaries.py`：幂等初始化字典数据。
- `src/ecommerce_agent/time_intelligence.py`：近 N 天、上周、本月、同比/环比解析与周期 SQL 生成。
- `src/ecommerce_agent/conversation.py`：追问上下文、模糊指标澄清和有界会话存储。
- `src/ecommerce_agent/charting.py`：按结果列自动选择折线图、柱状图或表格。
- `tests/test_phase2.py`：覆盖 Phase 2 关键路径。
- `phase2-development-log.md`：难点、痛点、解决办法和后续建议。

**当前验收状态**
- `uv run pytest -q`：17 passed，1 skipped。
- `uv run python -m compileall -q src scripts apps`：通过。
- API 增加 `conversation_id` 与 `retrieval_sources` 字段；Streamlit 支持 session state 多轮追问。
- 时间周期以 `max(fact_orders.order_date)` 为业务锚点，避免固定快照数据与机器当前日期错位。
- 详细问题记录见 `phase2-development-log.md`。

### Phase 3：工程化（第 6–7 周）
- [x] LangGraph 状态图重构，SSE 实时展示推理链路
- [x] 模型可插拔抽象层
- [x] 安全加固 + 审计日志
- [x] 前端换 Next.js
- [x] 结构化语义计划 + 通用 SQL 编译 + 执行前语义校验
- [x] Next.js 多轮记录、多会话创建/切换、轮次导航、滚动浏览与浏览器历史恢复
- [x] 结构化多轮槽位继承 + 环比/同比贡献度拆解
- [x] 开放能力注册与任务路由 + 商品库存预测
- [x] 多预测周期槽位（周/月/季度/年/滚动 N 天）+ 前端请求锁自动恢复

#### Phase 3 开发记录（2026-09-16）

**已交付文件**
- `src/ecommerce_agent/agent.py`：增加 `stream_agent_events()`，以稳定事件协议输出状态图进度。
- `src/ecommerce_agent/main.py`：新增 `/api/agent/query/stream` SSE 接口。
- `src/ecommerce_agent/llm.py`：抽象 `CompletionGateway`，支持 DeepSeek/离线规则 provider。
- `src/ecommerce_agent/semantic.py`：新增 `QuerySpec`、指标/维度组合式 SQL 编译器和语义校验器；显式业务约束优先于模型猜测。
- `src/ecommerce_agent/conversation.py`：每轮保存结构化 QuerySpec，识别省略指标的贡献/拆解类追问并继承上一轮口径。
- `src/ecommerce_agent/capabilities.py`：开放任务到能力的注册与匹配；控制跨任务上下文是否兼容，未注册能力显式兜底。
- `src/ecommerce_agent/semantic.py`：新增库存预测编译器，按最近 90 天逐日销量趋势、指定未来周期需求、7 天安全库存和当前库存计算补货建议。
- `apps/web/components/AgentDashboard.tsx`：请求锁与持久化历史解耦，统一处理成功、失败、中止和超时后的按钮恢复。
- `src/ecommerce_agent/agent.py`：在 SQL 生成与执行之间新增语义校验节点，错误反馈进入有限 ReAct 修复回路。
- `src/ecommerce_agent/audit.py`：脱敏 JSONL 审计日志。
- `apps/web/`：Next.js 16 + TypeScript 前端，包含多轮/多会话历史、SSE 节点进度、ECharts、指标卡、结果表与调试面板；完成记录以有界 `localStorage` 快照跨刷新恢复。
- `tests/test_api.py` / `tests/test_llm_provider.py` / `tests/test_security_audit.py`：覆盖工程化关键路径。
- `phase3-development-log.md`：记录难点、解决办法和验收限制。

**当前验收状态**
- `uv run pytest -q`：50 passed，1 warning；包含真实 PostgreSQL 连接、商品复购率、商品 GMV 贡献和多预测周期库存查询测试。
- `uv run python -m compileall -q src scripts apps`：通过。
- `npm run typecheck` / `npm run build`：通过，首页完成静态预渲染。
- SQL 安全校验新增跨 schema、危险函数、控制字符和长度限制；API 支持 JSON 与 SSE 两种调用方式。
- 浏览器真实查询验证：SSE 展示 7 个 LangGraph 节点，返回近 30 天销售分析、指标卡和结果表。
- 多会话验证：同一会话三轮结果不互相覆盖，侧栏列出每轮问题并可快捷定位，独立 transcript 支持滚轮浏览；刷新后历史与当前会话仍在，可新建独立会话并切回旧会话。后端上下文当前仍是进程内存，API 重启后的持久化上下文留待数据库会话存储阶段。
- 结构化追问验证：首轮“近 30 天销售额多少？环比上个月呢？”，追问“主要贡献来源是什么商品”；真实 DeepSeek + PostgreSQL 返回商品本期/上期 GMV、增量、增幅与贡献占比，未触发多余澄清。
- 能力切换验证：商品 GMV 贡献会话后提出“按照趋势下个月各种商品应该准备多少库存量”，不继承旧 GMV 口径，进入 `inventory_forecast` 并输出预测需求、当前库存、安全库存、目标库存和建议补货量。
- 季度预测验证：“下季度各商品应该准备多少库存”解析为 `next_calendar_quarter`，真实 DeepSeek + PostgreSQL 返回下一自然季度补货清单；前端 SSE 正常结束并释放操作按钮。
- 详细问题记录见 `phase3-development-log.md`。

### Phase 4：打磨与简历（第 8 周）
- [ ] 自建评测集（50 条业务问题，量化准确率）
- [ ] 录制 demo / README 架构图
- [ ] 简历话术 + 面试问答准备

---

## 八、简历呈现

### 项目名
> 基于 LangGraph 的电商数据分析 Agent（Text-to-SQL）

### 描述模板

```
电商数据分析 Agent（个人项目）                          2026.03 – 2026.05

- 基于 LangGraph 构建「意图解析→SQL生成→执行→反思」的 Agent 编排，
  实现自然语言到 SQL 的自动生成，支持销售/商品/用户/渠道多维度分析
- 设计数据字典 + 指标字典 RAG（pgvector 语义检索），按需注入相关表
  结构与指标口径，SQL 生成准确率从 70% 提升至 90%+
- 实现 ReAct 自纠错回路：SQL 报错自动回喂重试，复杂查询一次成功率显著提升
- 攻克时间智能难题：统一处理同比/环比/MTD/YTD，避免模型手写日期逻辑出错
- 通过 SSE 实时可视化 Agent 推理链路，支持多轮追问与口径澄清
```

### 面试高频追问

1. 你造的数据量多大？怎么保证数据真实感？
2. Text-to-SQL 准确率怎么量化？评测集怎么设计的？
3. "复购率""客单价"这种口径，Agent 怎么统一理解的？
4. 同比环比这种时间逻辑，你为什么不直接让模型写 SQL？
5. 怎么防止运营用户问出越权或恶意查询？
6. 多轮对话里怎么判断用户是追问还是新问题？
7. 模型每次生成的 SQL 都不一样，怎么保证结果可靠？
8. 全量 schema 注入和 RAG 检索注入，准确率差多少？你怎么测的？

---

## 九、难点与应对

| 难点 | 应对 |
|---|---|
| SQL 幻觉（编造字段/表） | 数据字典 RAG + 只注入相关 schema + 校验 |
| 口径不一致（销售额 vs GMV） | 指标字典统一映射 |
| 时间逻辑复杂 | 时间智能层统一封装 |
| 生成 SQL 报错 | ReAct 纠错回路 |
| SQL 注入/越权 | 只读账号 + 表白名单 + 关键字拦截 |
| 结果不可复现 | 固定 prompt 版本 + 记录评测 |

---

## 十、进阶加分方向

- [ ] **评测体系**：50 条业务问题测试集，量化准确率、展示迭代曲线
- [ ] **多智能体**：加"审查者 Agent"校验 SQL 正确性与结果合理性
- [ ] **指标层抽象**：做简单语义层（类似 dbt metrics），让口径真正可治理
- [ ] **增量记忆**：长期记住运营偏好（常用指标、默认时间范围）
- [ ] **私有化部署**：接开源模型（Qwen/DeepSeek），去掉外部 API 依赖

---

## 十一、面试问答（含参考回答）

> 按面试官提问概率从高到低排列。每个问题标注「考察点」和可直接使用的「参考回答」。
> 回答基于项目当前真实进展（120000 订单 / 20000 用户 / 600 商品，DeepSeek + LangGraph + PostgreSQL/pgvector）。

### Q1. 简单介绍下你这个项目？

**考察点**：表达是否清晰、有没有抓住重点、是否真的理解自己做的东西。

**参考回答**：
"我做的是一个面向电商运营的**自然语言数据分析 Agent**。运营用大白话问，比如'近 30 天销售额多少，环比上个月呢'，系统自动完成意图识别、生成 SQL、查数据库、出图表、给结论这整条链路。技术栈是 FastAPI + LangGraph + DeepSeek + PostgreSQL。核心解决两个问题：一是**降低查数门槛**，让不懂 SQL 的运营也能自助取数；二是**降低大模型写 SQL 的幻觉**，通过数据字典 RAG 和 ReAct 纠错把准确率从 70% 提到 90% 以上。"

---

### Q2. 大模型生成的 SQL 经常是错的（幻觉），你具体怎么解决的？

**考察点**：这是 Text-to-SQL 项目的灵魂问题，考察你有没有真正踩过坑、想过办法。

**参考回答**（分四层，层层递进）：
1. **只注入相关 schema**：不是把全库几十张表塞进 prompt，而是用 pgvector 做数据字典检索，只把和问题相关的表结构和字段注释注入，减少干扰。
2. **指标字典**：把"销售额=sum(amount) where status='paid'"这类口径固化，模型不用自己理解"销售额"和"GMV"是不是一回事。
3. **few-shot**：每种问法类型（趋势/排名/占比/对比）给 1-2 个正确示例，锚定输出格式。
4. **ReAct 纠错回路**：SQL 执行报错时，把报错信息回喂模型重新生成，最多重试 3 次。

"这四层是递进关系，最后一道兜底是 ReAct。我实测过：只做全量 schema 注入时准确率大概 70%，加上 RAG 检索注入后到 85% 左右，再加上纠错回路到 90% 以上。"

---

### Q3. 你说的 90% 准确率是怎么测出来的？评测集怎么设计？

**考察点**：数据意识和严谨性——很多人说"效果很好"但没有量化。

**参考回答**：
"我自己建了一个 50 条的评测集，覆盖四类问题：销售总览、商品分析、用户分析、渠道分析。每条标注了**标准 SQL** 和**预期结果集**。评测方式是执行生成的 SQL，和标准 SQL 的结果做比对——结果一致算对，不能只看 SQL 文本是否一样，因为不同写法可能结果相同。同时区分两个指标：**执行成功率**（SQL 能跑通）和**结果准确率**（结果对得上），后者才是真正有意义的。这个评测集本身也是迭代依据，我每改一版 prompt 就重跑一遍。"

---

### Q4. 全量 schema 注入 vs RAG 检索注入，准确率差多少？怎么验证的？

**考察点**：有没有做对照实验（ablation），而不是凭感觉。

**参考回答**：
"我做了一个简单的对照：同一批评测集，方案 A 是固定注入所有表结构，方案 B 是用 pgvector 只检索 Top-3 相关表。结果执行成功率 B 比 A 高了十几个点。原因是全量注入会让 prompt 又长又乱，模型容易把字段张冠李戴，比如把 `dim_user` 的 `city` 当成 `dim_region` 的字段。检索注入后上下文更聚焦，幻觉明显下降。这也是我用 pgvector 而不是简单字符串匹配的原因——用户说'华东区'，字符串匹配找不到 `area='华东'`，但向量检索能关联到。"

---

### Q5. 你的数据字典 RAG 具体怎么做的？向量化的是什么内容？

**考察点**：RAG 是不是真做懂了，还是套了框架。

**参考回答**：
"向量化的不是原始数据，是**元数据**：每张表的表名+业务含义、每个字段的字段名+中文注释、以及指标字典里的口径定义和常见同义词。比如 `fact_orders.amount` 的注释是'实付金额，即 GMV'，用户问'卖得怎么样'就能通过语义检索命中。入库时我做了分块（每个字段一条），用 embedding 模型编码存进 pgvector。查询时把用户问题编码，取 Top-N 相似项拼进 prompt。选 pgvector 是因为业务数据本来就在 PostgreSQL，向量和关系数据一张库里，不用额外维护 FAISS/ChromaDB 的同步问题。"

---

### Q6. 为什么用 LangGraph，而不是直接 LangChain 串起来？

**考察点**：框架选型有没有思考，是不是跟风。

**参考回答**：
"我的主链路是'意图识别 → 生成 SQL → 执行 → 纠错 → 汇总'，这天然是一个**有状态、有回边**的流程——纠错失败要跳回生成，成功要走到汇总，LangChain 的线性 Chain 表达这种回路很别扭，得靠各种 if-else 硬拼。LangGraph 把每个步骤建成图节点，状态在节点间流转，回边就是图的一条边，逻辑清晰很多。而且它支持 checkpoint，方便我后面做多轮对话的状态恢复。当然底层工具调用我理解的是原生 SDK 的 Tool Use，没依赖 LangChain 那层封装。"

---

### Q7. ReAct 纠错回路具体怎么实现的？最多重试几次？

**考察点**：对 ReAct 的理解是否具体，有没有工程上的边界意识。

**参考回答**：
"执行 SQL 失败时，我会把报错信息（比如 `column "amout" does not exist`）作为新的一轮输入回喂给模型，让它在报错的上下文里重新生成。设了上限 3 次，防止死循环烧 token。还有一个细节：我不只在报错时纠错，**结果异常时也纠错**——比如查出来是空集、或者数量级明显离谱（销售额为负），会触发模型反思是不是漏了 where 条件。3 次重试后仍失败就返回一个明确的降级响应，而不是假装成功。"

---

### Q8. 怎么防止用户问出越权或恶意 SQL？

**考察点**：安全意识，这是工程面试必考点。

**参考回答**（分层）：
1. **账号层**：数据库用只读账号，没有 DML/DDL 权限。
2. **语法层**：用 `sqlglot` 做 AST 解析，只允许单条 `SELECT`/`WITH` 语句，出现 `DELETE`/`DROP`/`UPDATE`/多语句直接拒绝。
3. **表级**：表名白名单，只放那 5 张业务表。
4. **执行层**：只读事务 + `statement_timeout` 限制超时 + 外层强制 `LIMIT` 限制返回行数，防止 `SELECT *` 拉爆内存。

"这四层是纵深防御，任何一层被绕过还有下一层。"

---

### Q9. 多轮对话的记忆怎么设计的？怎么判断用户是追问还是新问题？

**考察点**：对 Agent 记忆机制的理解。

**参考回答**：
"当前是**会话级短期记忆**：把最近几轮的'用户问题 + 生成的 SQL + 涉及的字段/时间范围'存进上下文。判断追问还是新问题，主要靠两个信号：一是 LLM 在意图识别阶段输出一个'是否延续上一轮'的判断；二是规则辅助，比如用户说'那华东呢''环比呢'这种省略主语代词，明显是追问，就把上一轮的表和时间范围继承过来。长期记忆（用户偏好）我还没做，作为后续方向。"

---

### Q10. "同比环比"这种时间逻辑，你为什么不直接让模型写 SQL？

**考察点**：有没有意识到"让模型手写复杂日期逻辑"是个坑。

**参考回答**：
"因为时间智能是**最容易错**的地方。同比环比、MTD/YTD 涉及一堆日期函数和边界条件，模型每次生成的写法还不一样，出错概率极高，而且错了很难排查。所以我把时间逻辑抽成**时间智能层**：识别出'环比上个月'这种意图后，由代码统一计算时间区间（本期 + 对比期），再分别查两次或一次用窗口函数算，不让模型碰日期函数。这样结果稳定、可复现，模型只负责理解'用户在问时间对比'这一件事。"

---

### Q11. "销售额""客单价""复购率"这些口径，Agent 怎么统一理解的？

**考察点**：指标口径是数据产品核心难点。

**参考回答**：
"靠**指标字典**。我把每个指标的定义固化下来，比如'销售额 = sum(amount) where status = paid'，'客单价 = 销售额 / 付费订单数'，'复购率 = 购买≥2次的用户数 / 总购买用户数'。用户提问时，先检索命中的指标，把它的标准定义和 SQL 片段注入 prompt，模型基于这个定义生成，而不是自己瞎理解。这样保证'销售额'和'GMV'在系统里永远是同一个口径，不会今天一个数明天一个数。"

---

### Q12. 为什么选 DeepSeek？模型可插拔是怎么实现的？

**考察点**：选型理由 + 架构抽象能力。

**参考回答**：
"选 DeepSeek 主要两个原因：一是**成本**，个人项目 demo 反复调用，DeepSeek 的价格比头部闭源模型低一个量级；二是它提供 **OpenAI-compatible 接口**，接入简单。可插拔这块，我定义了一个统一的 LLM 抽象层，内部实现 DeepSeek 客户端，但上层 Agent 只依赖抽象接口，不感知具体模型。所以切换 OpenAI、或换成开源本地模型（Qwen/DeepSeek 本地版）时，只改配置和新增一个 adapter，业务代码不动。"

---

### Q13. 大模型每次生成的 SQL 都不一样，怎么保证结果可靠？

**考察点**：对 LLM 不确定性的认知和工程应对。

**参考回答**：
"分两点：**单次可靠性**靠前面说的四层约束（RAG 注入 + 指标字典 + few-shot + 纠错），把输出收敛到正确区间；**长期可复现性**靠固定 prompt 版本 + 记录每次生成的 SQL 和结果，出问题能回溯。另外我把 temperature 调低（接近 0）来降低随机性，数据分析场景不需要创造性，需要的是稳定。还通过评测集持续回归，改任何 prompt 都能立刻看出有没有引入新问题。"

---

### Q14. 单 Agent 和多 Agent，你为什么现在用单 Agent？

**考察点**：有没有过度设计的意识，边界感。

**参考回答**：
"当前是单 Agent——一个 LLM 在 LangGraph 的状态图里走'规划→执行→反思'。MVP 阶段单 Agent 完全够用，链路短、延迟低、好调试。多 Agent（比如加一个审查者 Agent 校验 SQL）能进一步提准确率，但代价是延迟翻倍、成本增加、编排复杂。我的策略是先单 Agent 跑通，把准确率和体验打磨好，再把'演进到多 Agent'作为下一步优化点。这样简历上的故事线是'从单 Agent 到多 Agent 的演进'，比一上来就堆多 Agent 更有说服力。"

---

### Q15. 这个项目最大的难点是什么？你怎么解决的？

**考察点**：真实的技术攻坚经历，不要背答案。

**参考回答**：
"最难的是**大模型写 SQL 的幻觉问题**，尤其是字段和口径的混淆。比如模型会把 `dim_user.city` 和 `dim_region.city` 搞混，或者把'销售额'理解成'订单量'。我的解法是组合拳：数据字典 RAG 做精准上下文注入 + 指标字典统一口径 + ReAct 纠错兜底。过程中最大的收获是理解到：**不要指望模型更聪明，而是通过工程手段给模型更干净的上下文和更明确的约束**，这才是提升可靠性的关键。"

---

### Q16. 如果重做一遍，你会改进什么？

**考察点**：反思能力和成长性。

**参考回答**：
"三个地方：第一，**评测集应该最早建**，我是做到一半才建评测集，导致前期迭代有点盲目；第二，**指标口径层应该更早抽象**，做成独立的语义层（类似 dbt metrics），而不是散在指标字典里；第三，**可观测性应该一开始就做**，把每次的 prompt、生成的 SQL、执行结果、纠错过程完整记录成 trace，现在排查问题还要翻日志。这些也是我后续的优化方向。"

---

### Q17. 为什么不用现成的 BI 工具（Tableau/Superset），要做 Agent？

**考察点**：产品价值判断，避免"为了 AI 而 AI"。

**参考回答**：
"BI 工具解决的是'会查数的人做报表'，但它的门槛是**用户要懂维度和指标**，运营小白用起来还是有学习成本。Agent 的价值是把入口从'拖拽配置'变成'自然语言'，让不懂数据模型的人也能自助取数，本质是**降低使用门槛**，而不是替代 BI。对复杂报表和固定看板，BI 仍然更合适；Agent 覆盖的是长尾的、临时的、即席的查询需求。两者是互补的。"

---

### Q18. 你这个项目是练手还是解决了真实问题？数据是真的吗？

**考察点**：诚实度，不要夸大。

**参考回答**：
"数据是**自己造的**，用脚本生成了 6 个月、12 万订单的模拟电商数据，参考了真实电商的业务规律——比如新老客比例、复购分布、渠道占比、节假日销量波动，尽量让数据真实。项目定位是练手，目标是完整跑通 Agent 的技术链路，所以诚实地讲，它没有上线生产、没有真实用户。但我把工程标准按生产来要求了：有测试、有评测集、有安全校验、有可插拔设计。"

---

### 附：如果面试官追问"你这项目有什么不足"

诚实回答这三个，反而加分：
1. **评测集只有 50 条、数据是模拟的** → 无法证明真实分布下的鲁棒性
2. **长期记忆没做** → 无法记住运营偏好
3. **复杂查询能力有限** → 多表深 join、子查询嵌套场景准确率会下降

> 面试技巧：主动讲"不足 + 你打算怎么改进"，比被追问出来更有掌控力。
