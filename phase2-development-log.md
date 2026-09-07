# Phase 2 开发记录：准确率与体验

日期：2026-09-07  
分支：`phase2/accuracy-experience`

## 已完成

- 数据字典和指标字典：新增确定性的本地词法检索，按问题只注入相关字段、同义词和指标 SQL 口径；保留 `embedding vector(1536)` 与 pgvector cosine 检索接口，后续接入 embedding 服务时无需改 Agent 协议。
- 时间智能：统一解析“近 N 天、上周、本月、同比、环比”等表达；滚动窗口按天数计算，月/周/年使用日历边界，以事实表最大日期为锚点生成当前周期和对比周期，避免把当前机器日期误当成业务数据日期。
- 多轮对话：API 接受 `conversation_id`，保存最近 20 轮上下文；Streamlit 使用 session state 保存上下文；“那环比呢？”等追问会自动带上上一轮问题。
- 意图澄清：对“上个月卖得怎么样”这类没有指标口径的问题先反问销售额、订单量、销量还是客单价，不直接执行猜测 SQL。
- 自动图表和摘要：根据日期列、类别列和数值列自动选择折线图/柱状图/表格；原有自然语言总结继续作为主回答，SQL 和 RAG 命中项保留在调试区。
- 数据库初始化：新增 `uv run python scripts/seed_dictionaries.py`，向 `data_dictionary` 和 `metric_dictionary` 幂等写入内置口径。

## 痛点、难点与解决办法

| 问题 | 原因 | 解决办法 |
|---|---|---|
| 业务数据只有 2026-03 至 2026-08，直接用 `CURRENT_DATE` 会查不到数据 | Demo 数据是固定快照，运行日期会继续变化 | 时间智能统一使用 `max(fact_orders.order_date)` 作为锚点，并把周期计算封装在 `time_intelligence.py` |
| “上个月/上周”与“近 30/7 天”不是同一个口径 | 简单按天数回退会跨月、跨周，导致运营报表边界不一致 | 月/周/年使用 PostgreSQL `date_trunc` 日历边界；上月/上周先移动锚点，再计算对比周期 |
| 仅靠大模型生成指标容易出现“销售额/销量”混用 | 同义词和指标定义没有稳定注入 | 新增指标字典，固定表达式与过滤条件；RAG 只注入与当前问题相关的条目 |
| pgvector 需要 embedding 服务，离线 demo 无法稳定启动 | 本地没有强制配置 embedding API，且向量列可能为空 | 默认使用确定性的词法检索；提供 `search_pgvector()` 作为有 embedding 时的升级路径，空向量记录仍可正常使用 |
| “卖得怎么样”无法判断用户要看哪个指标 | 口语表达存在真实业务歧义 | 在 fallback 和模型计划解析前增加模糊指标检测，返回澄清问题而不是执行猜测查询 |
| 追问缺少上一轮的主语和指标 | API 原先是完全无状态请求 | 增加 `ConversationStore`、`conversation_id` 和 `history`，并限制保存 20 轮，避免上下文无限增长 |
| 图表字段由模型填写时可能与真实结果列不一致 | 生成计划和 SQL 结果可能漂移 | `build_chart_spec()` 先校验计划字段，失败后根据日期/类别/数值列推断，最终安全降级为表格 |
| 初始化字典重复执行会产生重复数据 | `data_dictionary` 原 schema 没有自然主键 | seed 脚本对数据字典按表名+列名存在性判断，对指标字典使用主键 upsert，保证可重复执行 |

## 验证结果

- `uv run pytest -q`：17 passed，1 skipped（PostgreSQL 不可用时跳过真实连接测试）。
- `uv run python -m compileall -q src scripts apps`：通过。
- 关键测试覆盖：同比/环比 SQL 周期、RAG 命中、模糊口径澄清、追问上下文、图表自动推断。

## 后续建议

1. 为 `data_dictionary` 和 `metric_dictionary` 接入实际 embedding 生成任务，并用真实向量填充 `embedding` 列。
2. 将 `ConversationStore` 替换为 Redis 或 PostgreSQL，以支持多实例部署。
3. 为 50 条业务问题建立基准集，分别比较全量 schema、词法 RAG 和向量 RAG 的 SQL/结果准确率。
