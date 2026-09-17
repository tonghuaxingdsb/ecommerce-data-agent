"use client";

import dynamic from "next/dynamic";
import type { AgentResult } from "@/lib/types";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

export function ResultChart({ result }: { result: AgentResult }) {
  const { chart, rows } = result;
  if (!rows.length || !chart?.x || !chart?.y || chart.type === "table") return null;

  const labels = rows.map((row) => String(row[chart.x!] ?? ""));
  const values = rows.map((row) => Number(row[chart.y!] ?? 0));
  const type = chart.type === "line" ? "line" : "bar";
  const option = {
    animationDuration: 650,
    color: ["#4f6df5", "#15b79e"],
    grid: { left: 20, right: 22, top: 44, bottom: 20, containLabel: true },
    tooltip: { trigger: "axis", backgroundColor: "#13213c", borderWidth: 0, textStyle: { color: "#fff" } },
    xAxis: { type: "category", data: labels, axisLine: { lineStyle: { color: "#dce3ef" } }, axisLabel: { color: "#64748b" } },
    yAxis: { type: "value", splitLine: { lineStyle: { color: "#edf1f7" } }, axisLabel: { color: "#64748b" } },
    series: [{ type, data: values, smooth: type === "line", symbolSize: 7, barMaxWidth: 34, areaStyle: type === "line" ? { opacity: 0.08 } : undefined }],
  };

  return (
    <section className="panel chart-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">VISUAL ANALYSIS</p>
          <h2>{chart.title || "数据趋势"}</h2>
        </div>
        <span className="chart-tag">{type === "line" ? "趋势图" : "对比图"}</span>
      </div>
      <div className="chart-canvas">
        <ReactECharts option={option} style={{ height: 330 }} notMerge lazyUpdate />
      </div>
    </section>
  );
}
