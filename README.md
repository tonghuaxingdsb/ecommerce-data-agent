# Ecommerce Data Analysis Agent

Phase 0 delivers the project skeleton for an ecommerce natural-language data analysis agent:

- PostgreSQL + pgvector schema for ecommerce orders, users, products, regions, dates and future RAG dictionaries.
- Synthetic ecommerce dataset generator with 3-6 months of data and 100k+ orders by default. CSV output defaults to `utf-8-sig` so Chinese text opens cleanly in Excel on Windows.
- FastAPI service skeleton wired to a LangGraph workflow.
- DeepSeek API gateway through the OpenAI-compatible SDK. It calls DeepSeek when `DEEPSEEK_API_KEY` is configured and returns a local mock response otherwise.
- Phase 1 MVP chain: Text-to-SQL, read-only SQL validation/execution, bounded ReAct retry, natural-language result analysis and a Streamlit demo.
- Phase 2 experience layer: dictionary retrieval, time-intelligence SQL, multi-turn clarification, automatic chart selection and result summaries.
- Phase 3 engineering layer: pluggable LLM providers, LangGraph SSE progress events, SQL hardening and JSONL audit logging.
- Composable semantic query layer: the LLM produces a grounded `QuerySpec` (metrics, dimensions, filters, time and ranking), then SQL is generated and semantically validated before execution.
- Next.js 16 frontend with persistent multi-conversation history, live Agent progress, ECharts visualizations, result tables and SQL/RAG diagnostics.

## Quick Start

```powershell
Copy-Item .env.example .env
docker compose up -d postgres
uv run python scripts/generate_seed_data.py
uv run python scripts/load_seed_data.py
uv run python scripts/seed_dictionaries.py
uv run ecommerce-agent-api
```

Set `DEEPSEEK_API_KEY` in `.env` before starting the API if you want live model calls. The default model is `deepseek-v4-pro` and the default base URL is `https://api.deepseek.com`.

API endpoints:

- `GET http://127.0.0.1:8000/health`
- `POST http://127.0.0.1:8000/api/agent/query`

Example request:

```json
{
  "question": "近 30 天销售额多少？环比上个月呢？"
}
```

The default seed command writes CSV files to `data/generated/` with 120000 orders, 20000 users, 600 products and 6 months of order history.

## Next.js Frontend

Run the FastAPI service first, then start the web app in another terminal:

```powershell
Set-Location apps/web
Copy-Item .env.example .env.local
npm install
npm run dev
```

Open `http://127.0.0.1:3000`. The frontend consumes `POST /api/agent/query/stream` with a fetch stream, renders LangGraph progress in real time, and displays business conclusions, metrics, ECharts, result tables and a collapsible debugging panel. One conversation can retain multiple question/result turns, while **新建对话** creates an independent `conversation_id`. The active conversation expands every turn in the sidebar; each question can be clicked to locate its result, and the fixed-height transcript supports mouse-wheel scrolling without moving the composer. The browser keeps at most 20 conversations and 50 turns per conversation in `localStorage`, including the selected conversation, so completed UI history survives a page refresh.

Production verification:

```powershell
npm run typecheck
npm run build
```

## Legacy Streamlit Demo

```powershell
uv run streamlit run apps/streamlit_app.py
```

The legacy demo calls the same LangGraph chain as the API. It remains available as a lightweight Python-only fallback.

## Phase 2 capabilities

- The data/metric dictionaries are retrieved by business-question keywords before SQL generation. The PostgreSQL schema includes pgvector columns and cosine indexes; local fallback retrieval keeps the demo runnable without an embedding service.
- Time expressions such as `近 7 天`, `上周`, `本月`, `环比` and `同比` are converted into bounded periods anchored to the latest date in `fact_orders`.
- Send the same `conversation_id` in successive API requests to ask follow-up questions. For example, ask `近 30 天销售额多少？` and then `那环比呢？`.
- Ambiguous questions such as `上个月卖得怎么样？` produce a clarification instead of guessing a metric.

See [phase2-development-log.md](phase2-development-log.md) for the implementation record, verification results and encountered problems/solutions.

## Phase 3 engineering capabilities

- `POST /api/agent/query/stream` returns Server-Sent Events: node-level trace events followed by a final result event.
- Set `LLM_PROVIDER=deepseek` for the OpenAI-compatible DeepSeek gateway or `LLM_PROVIDER=mock` for an offline deterministic provider.
- SQL validation additionally enforces a maximum length, rejects non-public schemas and blocks dangerous configuration/file functions.
- API requests append redacted audit records to `AUDIT_LOG_PATH` (default `logs/audit.jsonl`). The log stores a SQL hash and sanitized preview rather than raw literals.
- The Next.js frontend consumes POST-based SSE through `fetch()`/`ReadableStream`; FastAPI allows only the configured local `CORS_ORIGINS` by default.
- The frontend models `Conversation -> ChatTurn[]` instead of one global result. Every SSE event updates its originating turn, so a later question no longer overwrites earlier questions, progress, answers or tables.
- Conversation history is browser-local. A FastAPI restart clears the current in-memory model context even though the rendered history remains available in `localStorage`; durable cross-device/server-side history is a later persistence task.
- Each backend conversation turn stores its grounded `QuerySpec`. Follow-up questions inherit omitted metrics, time ranges, filters and comparison periods, while constraints explicitly stated in the new question take precedence.
- Contribution follow-ups such as `主要贡献来源是什么商品` are compiled as period-over-period decomposition queries (`current_gmv`, `previous_gmv`, `gmv_change`, `change_pct`, `contribution_pct`) rather than current-GMV rankings.
- Open-ended requests are matched against a capability registry instead of requiring one intent or SQL template per utterance. Registered capabilities currently include generic semantic SQL analysis and product inventory forecasting; unsupported forecasts return a capability/data explanation instead of silently becoming GMV queries.
- Inventory-planning questions support the next calendar week/month/quarter/year and rolling horizons such as `未来45天`. The capability uses the latest 90 days of daily paid units for a linear-trend baseline, adds seven days of safety stock and subtracts current stock. The response explicitly states that in-transit inventory and procurement lead time are unavailable and assumed to be zero.
- Inventory forecasts use a deterministic result formatter so calculated dates, quantities and assumptions cannot be truncated or altered by a second model call. Other model-written summaries pass a completeness gate (minimum length, balanced brackets and non-dangling endings) and automatically fall back to a grounded formatter when invalid.
- The frontend request lock is tab-local and tied to the actual active request rather than persisted chat history. Success, failure, manual abort and a 120-second timeout all release the action buttons.
- Questions are no longer routed from a single coarse intent directly to a fixed SQL template. Explicit user constraints are represented as `metrics + dimensions + filters + time_range + ranking`; the same metric can be combined with product, category, channel, region, membership level or date dimensions.
- A semantic validation node runs before SQL execution. It rejects executable-but-wrong queries such as answering product repurchase rate with GMV ranking, and sends precise validation feedback through the bounded repair loop.
- The offline provider uses the same semantic plan and SQL compiler. Its result banner identifies the active offline provider instead of incorrectly claiming that `DEEPSEEK_API_KEY` is missing.

Example semantic plan for `什么商品的复购率高`:

```json
{
  "metrics": ["repurchase_rate"],
  "dimensions": ["product"],
  "filters": [{"field": "status", "operator": "=", "value": "paid"}],
  "sort_by": "repurchase_rate",
  "sort_direction": "desc",
  "minimum_sample_size": 20
}
```

See [phase3-development-log.md](phase3-development-log.md) for engineering notes and verification limits.

## Phase 4 evaluation

The fixed benchmark in `src/ecommerce_agent/eval_cases.py`, exported to `data/evaluation/phase4_cases.jsonl`, contains **100 business questions plus 10 out-of-scope questions**. The business set has 90 executable ecommerce analyses (five metrics, rolling/calendar periods, product/category/channel/region/VIP breakdowns, ranking, daily trends, regional filters, period comparisons, growth contribution, inventory horizons, follow-ups and slow-moving products) and 10 requests that need unavailable data or tools. Unsupported requests must return a useful capability/data explanation with no SQL execution; unrelated requests must stay outside the SQL and LLM path.

Run the full agent graph against the seeded PostgreSQL database:

```powershell
uv run python -m scripts.evaluate_phase4 --provider mock --output reports/phase4-mock.json
uv run python -m scripts.evaluate_phase4 --provider deepseek --workers 4 --output reports/phase4-deepseek.json
uv run python -m scripts.evaluate_phase4 --export-cases data/evaluation/phase4_cases.jsonl
```

`deepseek` requires `DEEPSEEK_API_KEY`; `mock` is the deterministic offline regression baseline. A seeded PostgreSQL database is required. Reports include the dataset/prompt hashes, order count and latest order date, per-case failures and four different measures:

- **Plan accuracy:** all annotated capability, metric, dimension, time, filter, comparison, ranking and forecast slots match.
- **Execution/refusal rate:** supported questions execute a SQL query without SQL/semantic errors; unsupported questions return no SQL and a nonempty explanation.
- **Result equivalence:** all 90 executable cases compare relevant result columns against independently constructed reference results. Aggregate, comparison, contribution and slow-moving cases use reference SQL; inventory horizons recompute the 90-day trend and replenishment arithmetic in Python from daily source observations. Row order is ignored, and numeric values are compared at their reported precision.
- **Accuracy:** a case passes when every applicable check passes. Reports give separate business and out-of-scope accuracy. This is a fixed synthetic-data benchmark score, not a claim about production traffic or unseen questions.

The agent now routes missing-data business questions to `unsupported_business` and unrelated requests to `out_of_scope` before retrieval, model generation or SQL execution. Existing `unsupported_forecast` handling remains separate. The final benchmark and limitations are recorded in [phase4-development-log.md](phase4-development-log.md).

## SQL Safety

`execute_sql` is intentionally narrow for the MVP:

- Only one PostgreSQL `SELECT`/`WITH` query is allowed.
- Referenced tables must be in the project whitelist.
- Mutating statements and dangerous functions are blocked before execution.
- Queries run inside a read-only transaction with `statement_timeout`.
- Results are wrapped with an outer `LIMIT`, controlled by `SQL_MAX_ROWS`.
