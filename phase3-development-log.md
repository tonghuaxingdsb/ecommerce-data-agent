# Phase 3 开发记录：工程化

日期：2026-09-16  
分支：`phase3/engineering`

## 已完成

- LangGraph 状态图保留清晰节点边界，并新增 `stream_agent_events()`，只输出节点名、最新 trace 和最终结果，不泄漏完整 prompt/会话状态。
- FastAPI 新增 `POST /api/agent/query/stream`，通过 SSE 实时推送 Agent 推理进度，最后发送与 JSON API 一致的结果事件。
- LLM 可插拔：`CompletionGateway` 统一接口，`LLM_PROVIDER=deepseek` 使用 DeepSeek，`LLM_PROVIDER=mock` 使用离线规则 provider；请求超时配置化。
- SQL 安全加固：限制问题/SQL 长度，拒绝控制字符、非 public schema、危险函数和非只读语句，保留表白名单、只读事务、超时和行数上限。
- 审计日志：API 以 JSONL 追加记录 request、会话、意图、耗时、结果行数和 SQL 摘要；问题、错误和 SQL 字面量脱敏，原始 API key 不写入日志。
- Next.js 前端：新增 `apps/web`，使用 App Router、TypeScript 和 ECharts，展示实时节点进度、自然语言结论、指标卡、图表、结果表及折叠调试信息。

## 痛点、难点与解决办法

| 问题 | 解决办法 |
|---|---|
| SSE 若直接序列化 LangGraph 全状态，可能把历史和 prompt 暴露给客户端 | 只从每个节点更新中提取 node/trace，最终结果只保留 API 所需字段 |
| 既要支持真实模型又要保证离线 demo 可运行 | 使用 `CompletionGateway` 协议和显式 `RuleBasedGateway`，通过配置切换，不在业务节点中写 provider 分支 |
| SQL 表白名单可能被跨 schema 写法绕过 | 校验 `Table.db`，仅允许 `public` 或无 schema 的表引用；同时保留 AST 只读校验与危险函数黑名单 |
| 审计日志既要能排查问题又不能落敏感数据 | 记录 SQL hash + 脱敏预览，不记录完整 SQL 参数；对 question/error 做 key/token/password 脱敏和长度截断 |
| 多实例部署下本地 JSONL 不适合作为集中日志 | 当前定位为单机 demo 的 append-only 审计接口，后续可替换为结构化日志或日志管道，调用方协议不变 |
| 原生 `EventSource` 只支持 GET，现有 SSE 接口使用 POST 请求体 | 前端使用 `fetch()` + `ReadableStream` 手动解析 SSE frame，保留 question 和 conversation_id 的 JSON 请求格式 |
| ECharts 依赖浏览器 API，直接服务端渲染可能访问 `window` | 将图表封装为 Client Component，并用 `next/dynamic` 的 `ssr: false` 延迟加载 |
| Next.js 与 FastAPI 分别运行在 3000/8000 端口 | 后端加入显式 CORS allowlist，通过 `CORS_ORIGINS` 配置，不使用宽泛的 `*` |

## 验证结果

- `uv run pytest -q`：26 passed，1 warning（FastAPI 测试客户端依赖的上游弃用提示）；真实 PostgreSQL 连接测试已执行通过。
- `uv run python -m compileall -q src scripts apps`：通过。
- `npm run typecheck` 与 `npm run build`：通过；Next.js 16.3.5 成功生成静态首页。
- SSE、provider 选择、SQL 跨 schema/长度约束、审计脱敏均有自动化测试。
- 浏览器端到端验证：POST SSE 依次显示 6 个处理节点，并渲染真实 PostgreSQL 查询结果。
- PostgreSQL 容器已恢复运行，真实数据库连接与查询链路均已纳入本次验收。
