from __future__ import annotations

import pandas as pd
import streamlit as st

from ecommerce_agent.agent import agent_graph
from ecommerce_agent.conversation import ConversationTurn


st.set_page_config(page_title="电商数据分析 Agent", layout="wide")
st.title("电商数据分析 Agent")

if "conversation_history" not in st.session_state:
    st.session_state.conversation_history = []

examples = [
    "近 30 天销售额多少？环比上个月呢？",
    "Top10 商品是哪些？",
    "华东区老客复购率怎么样？",
    "哪个渠道销售额最高？",
    "近 30 天每日销售趋势",
    "滞销的商品有哪些？",
]

selected = st.selectbox("示例问题", examples, index=0)
question = st.text_input("问题", value=selected)
run_clicked = st.button("运行分析", type="primary")

if run_clicked and question.strip():
    with st.spinner("分析中"):
        result = agent_graph.invoke(
            {
                "question": question.strip(),
                "history": st.session_state.conversation_history,
                "trace": [],
            }
        )
        st.session_state.conversation_history.append(
            ConversationTurn(question=question.strip(), answer=result.get("answer", ""), intent=result.get("intent", ""))
        )

    st.subheader("分析结论")
    st.write(result.get("answer", ""))

    rows = result.get("rows", [])
    columns = result.get("columns", [])
    chart = result.get("chart", {})

    if rows:
        df = pd.DataFrame(rows, columns=columns)
        metric_cols = [column for column in columns if pd.api.types.is_numeric_dtype(df[column])]
        if metric_cols:
            metric_area = st.columns(min(4, len(metric_cols)))
            first_row = df.iloc[0]
            for column, area in zip(metric_cols[:4], metric_area, strict=False):
                area.metric(column, first_row[column])

        st.subheader("数据结果")
        st.dataframe(df, use_container_width=True, hide_index=True)

        chart_type = chart.get("type")
        x_column = chart.get("x")
        y_column = chart.get("y")
        if chart_type in {"bar", "line"} and x_column in df and y_column in df:
            chart_df = df[[x_column, y_column]].set_index(x_column)
            if chart_type == "line":
                st.line_chart(chart_df, use_container_width=True)
            else:
                st.bar_chart(chart_df, use_container_width=True)
    elif result.get("sql_error"):
        st.error(result["sql_error"])

    with st.expander("调试信息", expanded=False):
        st.caption("用于检查 Agent 流程，正式面向业务用户时可以隐藏。")
        st.code(result.get("executed_sql") or result.get("candidate_sql") or "", language="sql")
        st.caption("结构化查询计划")
        st.json(result.get("query_spec", {}))
        st.json(result.get("trace", []))
        st.caption("RAG 命中")
        st.write(result.get("retrieval_sources", []))
