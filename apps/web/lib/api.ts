import type { AgentHealth, AgentStreamEvent } from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const HEALTH_TIMEOUT_MS = 4000;

export async function fetchAgentHealth(signal: AbortSignal): Promise<AgentHealth | null> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal.aborted) return null;
  signal.addEventListener("abort", abort);
  const timeout = setTimeout(abort, HEALTH_TIMEOUT_MS);

  try {
    const response = await fetch(`${API_BASE_URL}/health`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) return null;
    const health = (await response.json()) as AgentHealth | null;
    if (health?.status !== "ok" || typeof health.llm_configured !== "boolean") return null;
    return health;
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
    signal.removeEventListener("abort", abort);
  }
}

export async function streamAgentQuery(
  question: string,
  conversationId: string,
  onEvent: (event: AgentStreamEvent) => void,
  signal?: AbortSignal,
) {
  const response = await fetch(`${API_BASE_URL}/api/agent/query/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ question, conversation_id: conversationId }),
    signal,
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(`请求失败（${response.status}）：${message || "未知错误"}`);
  }
  if (!response.body) throw new Error("浏览器未收到流式响应。请检查 API 是否已启动。");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) parseFrame(frame, onEvent);
    if (done) break;
  }
  if (buffer.trim()) parseFrame(buffer, onEvent);
}

function parseFrame(frame: string, onEvent: (event: AgentStreamEvent) => void) {
  const data = frame
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (!data) return;
  onEvent(JSON.parse(data) as AgentStreamEvent);
}
