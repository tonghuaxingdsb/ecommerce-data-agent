# Phase 3 开发记录：工程化

日期：2026-09-16  
分支：`phase3/engineering`

## 已完成

- LangGraph 状态图保留清晰节点边界，并新增 `stream_agent_events()`，只输出节点名、最新 trace 和最终结果，不泄漏完整 prompt/会话状态。
- FastAPI 新增 `POST /api/agent/query/stream`，通过 SSE 实时推送 Agent 推理进度，最后发送与 JSON API 一致的结果事件。
- LLM 可插拔：`CompletionGateway` 统一接口，`LLM_PROVIDER=deepseek` 使用 DeepSeek，`LLM_PROVIDER=mock` 使用离线规则 provider；请求超时配置化。
- SQL 安全加固：限制问题/SQL 长度，拒绝控制字符、非 public schema、危险函数和非只读语句，保留表白名单、只读事务、超时和行数上限。
- 审计日志：API 以 JSONL 追加记录 request、会话、意图、耗时、结果行数和 SQL 摘要；问题、错误和 SQL 字面量脱敏，原始 API key 不写入日志。

## 痛点、难点与解决办法

| 问题 | 解决办法 |
|---|---|
| SSE 若直接序列化 LangGraph 全状态，可能把历史和 prompt 暴露给客户端 | 只从每个节点更新中提取 node/trace，最终结果只保留 API 所需字段 |
| 既要支持真实模型又要保证离线 demo 可运行 | 使用 `CompletionGateway` 协议和显式 `RuleBasedGateway`，通过配置切换，不在业务节点中写 provider 分支 |
| SQL 表白名单可能被跨 schema 写法绕过 | 校验 `Table.db`，仅允许 `public` 或无 schema 的表引用；同时保留 AST 只读校验与危险函数黑名单 |
| 审计日志既要能排查问题又不能落敏感数据 | 记录 SQL hash + 脱敏预览，不记录完整 SQL 参数；对 question/error 做 key/token/password 脱敏和长度截断 |
| 多实例部署下本地 JSONL 不适合作为集中日志 | 当前定位为单机 demo 的 append-only 审计接口，后续可替换为结构化日志或日志管道，调用方协议不变 |

## 验证结果

- `uv run pytest -q`：24 passed，1 skipped，1 warning（FastAPI 测试客户端依赖的上游弃用提示）。
- `uv run python -m compileall -q src scripts apps`：通过。
- SSE、provider 选择、SQL 跨 schema/长度约束、审计脱敏均有自动化测试。
- PostgreSQL 真实连接测试仍因本机未运行 Docker 服务而跳过；启动 `docker compose up -d postgres` 后可运行完整集成测试。

