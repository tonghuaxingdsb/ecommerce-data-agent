export type Scalar = string | number | boolean | null;

export interface ChartSpec {
  type?: "bar" | "line" | "pie" | "table" | string;
  x?: string | null;
  y?: string | null;
  title?: string;
  data_points?: number;
}

export interface AgentResult {
  question: string;
  intent: string;
  answer: string;
  sql: string;
  columns: string[];
  rows: Record<string, Scalar>[];
  row_count: number;
  elapsed_ms: number;
  sql_error: string;
  semantic_error: string;
  query_spec: Record<string, unknown>;
  chart: ChartSpec;
  trace: string[];
  retrieval_sources: string[];
}

export interface StreamTraceEvent {
  event: "trace";
  node: string;
  trace: string;
}

export interface StreamResultEvent {
  event: "result";
  node: string;
  result: AgentResult;
}

export type AgentStreamEvent = StreamTraceEvent | StreamResultEvent;
