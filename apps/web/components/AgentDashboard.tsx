"use client";

import { FormEvent, useMemo, useRef, useState } from "react";
import { streamAgentQuery } from "@/lib/api";
import type { AgentResult, AgentStreamEvent, Scalar } from "@/lib/types";
import { ResultChart } from "./ResultChart";

const EXAMPLES = ["近 30 天销售额多少？环比上个月呢？", "Top10 商品是哪些？", "华东区老客复购率怎么样？", "近 30 天每日销售趋势"];
const NODE_LABELS: Record<string, string> = {
  classify_intent: "识别意图",
  prepare_context: "检索口径",
  generate_sql: "生成查询",
  execute_sql: "执行 SQL",
  prepare_retry: "纠错重试",
  summarize_result: "生成洞察",
  finalize: "整理结果",
};

export function AgentDashboard() {
  const [question, setQuestion] = useState(EXAMPLES[0]);
  const [result, setResult] = useState<AgentResult | null>(null);
  const [progress, setProgress] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const conversationId = useRef("");
  const abortController = useRef<AbortController | null>(null);

  const metrics = useMemo(() => {
    if (!result?.rows.length) return [];
    return result.columns
      .filter((column) => typeof result.rows[0][column] === "number")
      .slice(0, 4)
      .map((column) => ({ label: column, value: result.rows[0][column] }));
  }, [result]);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const normalized = question.trim();
    if (!normalized || running) return;
    if (!conversationId.current) conversationId.current = crypto.randomUUID();
    abortController.current = new AbortController();
    setRunning(true);
    setError("");
    setProgress([]);
    setResult(null);
    try {
      await streamAgentQuery(normalized, conversationId.current, handleEvent, abortController.current.signal);
    } catch (caught) {
      if ((caught as Error).name !== "AbortError") setError(caught instanceof Error ? caught.message : "分析失败，请稍后重试。");
    } finally {
      setRunning(false);
    }
  }

  function handleEvent(event: AgentStreamEvent) {
    if (event.event === "trace") setProgress((current) => [...current, event.node]);
    if (event.event === "result") setResult(event.result);
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">C</span>
          <div><strong>Commerce Lens</strong><span>电商数据分析 Agent</span></div>
        </div>
        <div className="system-status"><span className="status-dot" />Agent online</div>
      </header>

      <div className="dashboard-grid">
        <aside className="sidebar">
          <div className="side-intro">
            <p className="eyebrow">ASK YOUR DATA</p>
            <h1>把业务问题，变成清晰答案。</h1>
            <p>自然语言提问，实时完成口径检索、SQL 查询、图表分析和运营结论。</p>
          </div>
          <div className="examples">
            <span>试试这些问题</span>
            {EXAMPLES.map((example) => <button key={example} onClick={() => setQuestion(example)}>{example}<i>↗</i></button>)}
          </div>
          <div className="capability-card">
            <span className="capability-icon">✦</span>
            <div><strong>安全数据查询</strong><p>只读 SQL · 指标口径 RAG · 自动纠错</p></div>
          </div>
        </aside>

        <section className="workspace">
          <form className="query-card" onSubmit={submit}>
            <div className="query-label"><span>分析问题</span><small>{question.length}/2000</small></div>
            <textarea
              aria-label="分析问题"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void submit();
                }
              }}
              maxLength={2000}
              rows={3}
              placeholder="例如：近 30 天销售额多少？环比上个月呢？"
            />
            <div className="query-actions">
              <span>↵ Enter 提交 · 支持多轮追问</span>
              {running ? <button type="button" className="stop-button" onClick={() => abortController.current?.abort()}>停止</button> : <button type="submit" className="run-button">开始分析 <i>→</i></button>}
            </div>
          </form>

          {(running || progress.length > 0) && <ProgressSteps nodes={progress} running={running} />}
          {error && <div className="error-banner"><strong>分析未完成</strong><span>{error}</span></div>}
          {!result && !running && !error && <EmptyState />}

          {result && <>
            <section className="answer-card">
              <div className="answer-icon">✦</div>
              <div><p className="eyebrow">AGENT INSIGHT</p><h2>分析结论</h2><p className="answer-text">{result.answer}</p></div>
            </section>
            {metrics.length > 0 && <div className="metric-grid">{metrics.map((metric) => <div className="metric-card" key={metric.label}><span>{formatLabel(metric.label)}</span><strong>{formatValue(metric.value)}</strong></div>)}</div>}
            <ResultChart result={result} />
            <ResultTable result={result} />
            <DebugPanel result={result} />
          </>}
        </section>
      </div>
    </main>
  );
}

function ProgressSteps({ nodes, running }: { nodes: string[]; running: boolean }) {
  const unique = [...new Set(nodes)];
  return <section className="progress-card"><div><span className={running ? "spinner" : "done-mark"}>{running ? "" : "✓"}</span><div><strong>{running ? "Agent 正在分析" : "分析链路完成"}</strong><p>{running ? "数据正在穿过 LangGraph 工作流" : `已完成 ${unique.length} 个处理节点`}</p></div></div><div className="step-row">{unique.map((node, index) => <span key={`${node}-${index}`} className="step-chip">{NODE_LABELS[node] || node}</span>)}</div></section>;
}

function EmptyState() {
  return <section className="empty-state"><div className="orb"><span>⌁</span></div><h2>你的数据，随问随答</h2><p>选择一个示例，或者输入你关心的销售、商品、用户和渠道问题。</p><div><span>销售趋势</span><span>商品排行</span><span>用户复购</span><span>渠道表现</span></div></section>;
}

function ResultTable({ result }: { result: AgentResult }) {
  if (!result.rows.length) return null;
  return <section className="panel"><div className="panel-heading"><div><p className="eyebrow">QUERY RESULT</p><h2>明细结果</h2></div><span>{result.row_count} 行 · {result.elapsed_ms} ms</span></div><div className="table-wrap"><table><thead><tr>{result.columns.map((column) => <th key={column}>{formatLabel(column)}</th>)}</tr></thead><tbody>{result.rows.map((row, rowIndex) => <tr key={rowIndex}>{result.columns.map((column) => <td key={column}>{formatValue(row[column])}</td>)}</tr>)}</tbody></table></div></section>;
}

function DebugPanel({ result }: { result: AgentResult }) {
  return <details className="debug-panel"><summary><span>开发调试信息</span><small>SQL · Trace · RAG</small></summary><div className="debug-content"><div><label>Generated SQL</label><pre>{result.sql || "未生成 SQL"}</pre></div><div className="debug-columns"><div><label>Execution trace</label><ul>{result.trace.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div><div><label>RAG sources</label><ul>{result.retrieval_sources.map((item) => <li key={item}>{item}</li>)}</ul></div></div></div></details>;
}

function formatLabel(value: string) { return value.replaceAll("_", " "); }
function formatValue(value: Scalar | undefined) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);
  return String(value);
}
