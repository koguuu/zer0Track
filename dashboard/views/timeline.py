import html
import streamlit as st

BADGE_COLORS = {"PASS": "green", "FAIL": "red", "UNKNOWN": "orange"}


def render_timeline(case):
    """Render model-shaped case data. All untrusted text is escaped."""
    event = case["event"]
    location = event.get("handoff_type", "handoff").replace("_", " ").title()
    st.subheader(html.escape(case["shipment_id"]), icon=":material/package_2:")
    state = case.get("state", "unknown").replace("_", " ").title()
    st.badge(state, color={"Pending": "orange", "Accepted": "green", "Held": "red",
                           "Escalated": "violet", "Rejected": "gray"}.get(state, "gray"))
    st.caption(f"{location} handoff · Handler {html.escape(event['handler_id'])} · Case v{case['version']}")
    d = case["decision"]
    with st.container(border=True):
        st.markdown("##### :material/psychology: Agent assessment")
        st.markdown(f"**{html.escape(d['recommended_action'])}**")
        st.write(html.escape(d["reason"]))
        if d.get("unresolved_checks"):
            unresolved = ", ".join(html.escape(x.replace("_", " ")) for x in d["unresolved_checks"])
            st.caption(f"Unresolved · {unresolved}")
    if event.get("notes"):
        st.caption(f"Handoff note · {html.escape(event['notes'])}")
    if d["requires_human_approval"]:
        st.warning("Supervisor approval required before this case can move.")

    st.markdown("##### :material/rule: Mandatory checks")
    for ch in case["checks"]:
        confidence = ch.get("confidence")
        checker = ch["checker_name"].replace("_", " ").title()
        with st.container(border=True):
            check_head, badge_col = st.columns([3, 1], vertical_alignment="center")
            with check_head:
                st.markdown(f"**{html.escape(checker)}**")
            with badge_col:
                st.badge(ch["status"], color=BADGE_COLORS[ch["status"]])
            st.caption(html.escape(ch["detail"]))
            if confidence is not None:
                st.caption(f"Confidence · {confidence:.0%}")
            if ch.get("evidence_ids"):
                evidence = " · ".join(html.escape(x) for x in ch["evidence_ids"])
                st.caption(f"Evidence · {evidence}")

    approval = case.get("approval")
    if approval:
        with st.container(border=True):
            st.markdown("##### :material/fact_check: Supervisor record")
            st.caption(f"{html.escape(approval['timestamp'])} · Evidence v{approval['evidence_version']}")
            st.markdown(f"**{html.escape(approval['action'].title())}** by {html.escape(approval['operator_id'])}")
            st.write(html.escape(approval["reason"]))
