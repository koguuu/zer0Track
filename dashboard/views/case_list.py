import pandas as pd
import streamlit as st

STATUSES = ["all", "pending", "flagged", "escalated", "accepted", "rejected"]


def render_case_list(cases):
    """Searchable case table. Returns the selected shipment_id or None."""
    c1, c2 = st.columns([1.5, 1])
    q = c1.text_input("Search shipment ID", placeholder="e.g. GDX-1003", icon=":material/search:")
    status = c2.selectbox("Filter by status", STATUSES, format_func=str.title)

    rows = [
        {"Shipment": c["shipment_id"], "Status": c["status"].title(),
         "Handoff": c["event"].get("handoff_type", "").replace("_", " ").title(),
         "Handler": c["event"]["handler_id"], "Decision": c["decision"]["decision"].replace("_", " ").title()}
        for c in cases
        if q.lower() in c["shipment_id"].lower() and status in ("all", c["status"])
    ]
    if not rows:
        st.info("No cases match. Clear the search or pick another status.")
        return None

    df = pd.DataFrame(rows)
    event = st.dataframe(
        df,
        hide_index=True,
        width="stretch",
        height=min(480, 46 + 40 * len(rows)),
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Shipment": st.column_config.TextColumn("Shipment", width="medium"),
            "Status": st.column_config.TextColumn("Status", width="medium"),
            "Handoff": st.column_config.TextColumn("Handoff", width="medium"),
            "Handler": st.column_config.TextColumn("Handler", width="medium"),
            "Decision": st.column_config.TextColumn("Agent decision", width="medium"),
        },
    )
    sel = event.selection.rows
    return df.iloc[sel[0]]["Shipment"] if sel else None
