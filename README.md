# Ecommerce Data Analysis Agent

Phase 0 delivers the project skeleton for an ecommerce natural-language data analysis agent:

- PostgreSQL + pgvector schema for ecommerce orders, users, products, regions, dates and future RAG dictionaries.
- Synthetic ecommerce dataset generator with 3-6 months of data and 100k+ orders by default. CSV output defaults to `utf-8-sig` so Chinese text opens cleanly in Excel on Windows.
- FastAPI service skeleton wired to a LangGraph workflow.
- DeepSeek API gateway through the OpenAI-compatible SDK. It calls DeepSeek when `DEEPSEEK_API_KEY` is configured and returns a local mock response otherwise.
- Phase 1 MVP chain: Text-to-SQL, read-only SQL validation/execution, bounded ReAct retry, natural-language result analysis and a Streamlit demo.
- Phase 2 experience layer: dictionary retrieval, time-intelligence SQL, multi-turn clarification, automatic chart selection and result summaries.

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

## Streamlit Demo

```powershell
uv run streamlit run apps/streamlit_app.py
```

The demo calls the same LangGraph chain as the API. The main view shows the natural-language business conclusion, key metrics, result table and a basic chart when the result shape supports it. Generated SQL and trace are kept in the collapsed debug panel.

## Phase 2 capabilities

- The data/metric dictionaries are retrieved by business-question keywords before SQL generation. The PostgreSQL schema includes pgvector columns and cosine indexes; local fallback retrieval keeps the demo runnable without an embedding service.
- Time expressions such as `近 7 天`, `上周`, `本月`, `环比` and `同比` are converted into bounded periods anchored to the latest date in `fact_orders`.
- Send the same `conversation_id` in successive API requests to ask follow-up questions. For example, ask `近 30 天销售额多少？` and then `那环比呢？`.
- Ambiguous questions such as `上个月卖得怎么样？` produce a clarification instead of guessing a metric.

See [phase2-development-log.md](phase2-development-log.md) for the implementation record, verification results and encountered problems/solutions.

## SQL Safety

`execute_sql` is intentionally narrow for the MVP:

- Only one PostgreSQL `SELECT`/`WITH` query is allowed.
- Referenced tables must be in the project whitelist.
- Mutating statements and dangerous functions are blocked before execution.
- Queries run inside a read-only transaction with `statement_timeout`.
- Results are wrapped with an outer `LIMIT`, controlled by `SQL_MAX_ROWS`.
