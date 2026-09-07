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
- 会话记忆：记住上一轮的表、字段、时间范围
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
- [ ] 数据字典 + 指标字典 RAG（pgvector），准确率提升
- [ ] 时间智能（同比环比）
- [ ] 多轮对话 + 意图澄清
- [ ] 自动图表 + 结论摘要

### Phase 3：工程化（第 6–7 周）
- [ ] LangGraph 状态图重构，SSE 实时展示推理链路
- [ ] 模型可插拔抽象层
- [ ] 安全加固 + 审计日志
- [ ] 前端换 Next.js

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
