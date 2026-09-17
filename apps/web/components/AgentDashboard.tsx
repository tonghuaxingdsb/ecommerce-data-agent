"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { streamAgentQuery } from "@/lib/api";
import type { AgentResult, AgentStreamEvent, Scalar } from "@/lib/types";
import { ResultChart } from "./ResultChart";

const EXAMPLES = ["近 30 天销售额多少？环比上个月呢？", "Top10 商品是哪些？", "华东区老客复购率怎么样？", "近 30 天每日销售趋势"];
const STORAGE_KEY = "commerce-lens-conversations-v1";
const MAX_CONVERSATIONS = 20;
const MAX_TURNS = 50;
const NODE_LABELS: Record<string, string> = {
  classify_intent: "识别意图",
  prepare_context: "检索口径",
  generate_sql: "生成查询",
  validate_semantics: "校验语义",
  execute_sql: "执行 SQL",
  prepare_retry: "纠错重试",
  summarize_result: "生成洞察",
  finalize: "整理结果",
};

interface ChatTurn {
  id: string;
  question: string;
  progress: string[];
  running: boolean;
  error: string;
  result: AgentResult | null;
  createdAt: number;
}

interface Conversation {
  id: string;
  title: string;
  turns: ChatTurn[];
  createdAt: number;
  updatedAt: number;
}

export function AgentDashboard() {
  const [question, setQuestion] = useState(EXAMPLES[0]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState("");
  const [hydrated, setHydrated] = useState(false);
  const abortController = useRef<AbortController | null>(null);

  useEffect(() => {
    const restored = restoreConversations();
    const initial = restored.length > 0 ? restored : [createConversation()];
    setConversations(initial);
    setActiveConversationId(initial[0].id);
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
    } catch {
      // Keep the active session usable when browser storage is unavailable or full.
    }
  }, [conversations, hydrated]);

  const activeConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === activeConversationId) ?? null,
    [activeConversationId, conversations],
  );
  const running = conversations.some((conversation) => conversation.turns.some((turn) => turn.running));

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const normalized = question.trim();
    if (!normalized || running || !activeConversation) return;

    const conversationId = activeConversation.id;
    const turnId = crypto.randomUUID();
    const turn: ChatTurn = {
      id: turnId,
      question: normalized,
      progress: [],
      running: true,
      error: "",
      result: null,
      createdAt: Date.now(),
    };
    appendTurn(conversationId, turn);
    setQuestion("");
    abortController.current = new AbortController();

    try {
      await streamAgentQuery(
        normalized,
        conversationId,
        (streamEvent) => handleEvent(conversationId, turnId, streamEvent),
        abortController.current.signal,
      );
    } catch (caught) {
      if ((caught as Error).name !== "AbortError") {
        updateTurn(conversationId, turnId, (current) => ({
          ...current,
          error: caught instanceof Error ? caught.message : "分析失败，请稍后重试。",
        }));
      }
    } finally {
      updateTurn(conversationId, turnId, (current) => ({ ...current, running: false }));
      abortController.current = null;
    }
  }

  function appendTurn(conversationId: string, turn: ChatTurn) {
    setConversations((current) => current.map((conversation) => {
      if (conversation.id !== conversationId) return conversation;
      return {
        ...conversation,
        title: conversation.turns.length === 0 ? createTitle(turn.question) : conversation.title,
        turns: [...conversation.turns, turn].slice(-MAX_TURNS),
        updatedAt: Date.now(),
      };
    }));
  }

  function updateTurn(conversationId: string, turnId: string, updater: (turn: ChatTurn) => ChatTurn) {
    setConversations((current) => current.map((conversation) => {
      if (conversation.id !== conversationId) return conversation;
      return {
        ...conversation,
        turns: conversation.turns.map((turn) => turn.id === turnId ? updater(turn) : turn),
        updatedAt: Date.now(),
      };
    }));
  }

  function handleEvent(conversationId: string, turnId: string, streamEvent: AgentStreamEvent) {
    if (streamEvent.event === "trace") {
      updateTurn(conversationId, turnId, (turn) => ({ ...turn, progress: [...turn.progress, streamEvent.node] }));
    }
    if (streamEvent.event === "result") {
      updateTurn(conversationId, turnId, (turn) => ({ ...turn, result: streamEvent.result }));
    }
  }

  function startNewConversation() {
    if (running) return;
    const conversation = createConversation();
    setConversations((current) => [conversation, ...current].slice(0, MAX_CONVERSATIONS));
    setActiveConversationId(conversation.id);
    setQuestion(EXAMPLES[0]);
  }

  function selectConversation(conversationId: string) {
    if (running || conversationId === activeConversationId) return;
    setActiveConversationId(conversationId);
    setQuestion("");
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

          <nav className="conversation-nav" aria-label="对话历史">
            <div className="conversation-nav-heading">
              <span>对话记录</span>
              <button type="button" onClick={startNewConversation} disabled={running || !hydrated}>＋ 新建对话</button>
            </div>
            <div className="conversation-list">
              {conversations.map((conversation) => (
                <button
                  type="button"
                  className={conversation.id === activeConversationId ? "active" : ""}
                  key={conversation.id}
                  onClick={() => selectConversation(conversation.id)}
                  disabled={running}
                  title={conversation.title}
                >
                  <span>{conversation.title}</span>
                  <small>{conversation.turns.length} 轮</small>
                </button>
              ))}
            </div>
          </nav>

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
              {running
                ? <button type="button" className="stop-button" onClick={() => abortController.current?.abort()}>停止</button>
                : <button type="submit" className="run-button" disabled={!hydrated || !activeConversation}>开始分析 <i>→</i></button>}
            </div>
          </form>

          {!activeConversation?.turns.length && <EmptyState />}
          <div className="conversation-thread">
            {activeConversation?.turns.map((turn) => <ConversationTurnView turn={turn} key={turn.id} />)}
          </div>
        </section>
      </div>
    </main>
  );
}

function ConversationTurnView({ turn }: { turn: ChatTurn }) {
  return (
    <article className="conversation-turn">
      <div className="user-message"><span>YOU</span><p>{turn.question}</p></div>
      {(turn.running || turn.progress.length > 0) && <ProgressSteps nodes={turn.progress} running={turn.running} />}
      {turn.error && <div className="error-banner"><strong>分析未完成</strong><span>{turn.error}</span></div>}
      {turn.result && <ResultContent result={turn.result} />}
    </article>
  );
}

function ResultContent({ result }: { result: AgentResult }) {
  const metrics = result.rows.length > 0
    ? result.columns
      .filter((column) => typeof result.rows[0][column] === "number")
      .slice(0, 4)
      .map((column) => ({ label: column, value: result.rows[0][column] }))
    : [];
  return <>
    <section className="answer-card">
      <div className="answer-icon">✦</div>
      <div><p className="eyebrow">AGENT INSIGHT</p><h2>分析结论</h2><p className="answer-text">{result.answer}</p></div>
    </section>
    {metrics.length > 0 && <div className="metric-grid">{metrics.map((metric) => <div className="metric-card" key={metric.label}><span>{formatLabel(metric.label)}</span><strong>{formatValue(metric.value)}</strong></div>)}</div>}
    <ResultChart result={result} />
    <ResultTable result={result} />
    <DebugPanel result={result} />
  </>;
}

function ProgressSteps({ nodes, running }: { nodes: string[]; running: boolean }) {
  const unique = [...new Set(nodes)];
  return <section className="progress-card"><div><span className={running ? "spinner" : "done-mark"}>{running ? "" : "✓"}</span><div><strong>{running ? "Agent 正在分析" : "分析链路完成"}</strong><p>{running ? "数据正在穿过 LangGraph 工作流" : `已完成 ${unique.length} 个处理节点`}</p></div></div><div className="step-row">{unique.map((node, index) => <span key={`${node}-${index}`} className="step-chip">{NODE_LABELS[node] || node}</span>)}</div></section>;
}

function EmptyState() {
  return <section className="empty-state"><div className="orb"><span>⌁</span></div><h2>开启一段数据对话</h2><p>当前对话还没有消息。选择示例或输入业务问题开始分析。</p><div><span>销售趋势</span><span>商品排行</span><span>用户复购</span><span>渠道表现</span></div></section>;
}

function ResultTable({ result }: { result: AgentResult }) {
  if (!result.rows.length) return null;
  return <section className="panel"><div className="panel-heading"><div><p className="eyebrow">QUERY RESULT</p><h2>明细结果</h2></div><span>{result.row_count} 行 · {result.elapsed_ms} ms</span></div><div className="table-wrap"><table><thead><tr>{result.columns.map((column) => <th key={column}>{formatLabel(column)}</th>)}</tr></thead><tbody>{result.rows.map((row, rowIndex) => <tr key={rowIndex}>{result.columns.map((column) => <td key={column}>{formatValue(row[column])}</td>)}</tr>)}</tbody></table></div></section>;
}

function DebugPanel({ result }: { result: AgentResult }) {
  return <details className="debug-panel"><summary><span>开发调试信息</span><small>Plan · SQL · Trace · RAG</small></summary><div className="debug-content"><div><label>Query spec</label><pre>{JSON.stringify(result.query_spec, null, 2)}</pre></div><div><label>Generated SQL</label><pre>{result.sql || "未生成 SQL"}</pre></div><div className="debug-columns"><div><label>Execution trace</label><ul>{result.trace.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div><div><label>RAG sources</label><ul>{result.retrieval_sources.map((item) => <li key={item}>{item}</li>)}</ul></div></div></div></details>;
}

function createConversation(): Conversation {
  const now = Date.now();
  return { id: crypto.randomUUID(), title: "新对话", turns: [], createdAt: now, updatedAt: now };
}

function createTitle(question: string): string {
  const compact = question.replaceAll(/\s+/g, " ").trim();
  return compact.length > 24 ? `${compact.slice(0, 24)}…` : compact;
}

function restoreConversations(): Conversation[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(isStoredConversation)
      .slice(0, MAX_CONVERSATIONS)
      .map((conversation) => ({
        ...conversation,
        turns: conversation.turns.slice(-MAX_TURNS).map((turn) => ({
          ...turn,
          running: false,
          error: turn.running && !turn.result ? "页面刷新后，上一次分析已停止，请重新提交。" : turn.error,
        })),
      }));
  } catch {
    return [];
  }
}

function isStoredConversation(value: unknown): value is Conversation {
  if (!value || typeof value !== "object") return false;
  const conversation = value as Partial<Conversation>;
  return typeof conversation.id === "string"
    && typeof conversation.title === "string"
    && typeof conversation.createdAt === "number"
    && typeof conversation.updatedAt === "number"
    && Array.isArray(conversation.turns)
    && conversation.turns.every((turn) => Boolean(turn)
      && typeof turn.id === "string"
      && typeof turn.question === "string"
      && Array.isArray(turn.progress)
      && typeof turn.running === "boolean"
      && typeof turn.error === "string"
      && typeof turn.createdAt === "number");
}

function formatLabel(value: string) { return value.replaceAll("_", " "); }
function formatValue(value: Scalar | undefined) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);
  return String(value);
}
