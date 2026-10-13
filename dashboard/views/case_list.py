import pandas as pd
import streamlit as st

STATUSES = ["all", "pending", "flagged", "escalated", "accepted", "rejected"]


def render_case_list(cases):
    """Searchable case table. Returns the selected shipment_id or None."""
    c1, c2 = st.columns([2, 1])
    q = c1.text_input("Search shipment ID", placeholder="e.g. GDX-1003")
    status = c2.selectbox("Status", STATUSES)

    rows = [
        {"Shipment": c["shipment_id"], "Status": c["status"],
         "Location": c["event"].get("handoff_type", ""),
         "Handler": c["event"]["handler_id"], "Recommendation": c["decision"]["decision"]}
        for c in cases
        if q.lower() in c["shipment_id"].lower() and status in ("all", c["status"])
    ]
    if not rows:
        st.info("No cases match. Clear the search or pick another status.")
        return None

    df = pd.DataFrame(rows)
    event = st.dataframe(df, hide_index=True, use_container_width=True,
                         on_select="rerun", selection_mode="single-row")
    sel = event.selection.rows
    return df.iloc[sel[0]]["Shipment"] if sel else None
