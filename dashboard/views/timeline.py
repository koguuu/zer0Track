import html
import streamlit as st

COLORS = {"PASS": "#2F7D5B", "FAIL": "#B3402F", "UNKNOWN": "#B7791F"}
KIND = {"check": "#1F6F78", "tool": "#5A4E8C", "evidence": "#6B7280", "decision": "#1F2933"}


def _badge(status):
    return (f"<span style='background:{COLORS[status]};color:#fff;padding:2px 10px;"
            f"border-radius:4px;font-weight:600;font-size:0.85rem'>{status}</span>")


def render_timeline(case):
    """Render model-shaped case data. All untrusted text is escaped."""
    event = case["event"]
    location = event.get("handoff_type", "handoff")
    st.subheader(f"{case['shipment_id']} at {html.escape(location)}")
    d = case["decision"]
    st.markdown(f"**Recommendation:** {html.escape(d['recommended_action'])}")
    st.markdown(f"**Reason:** {html.escape(d['reason'])}")
    if event.get("notes"):
        st.markdown(f"**Note:** {html.escape(event['notes'])}")
    if d["requires_human_approval"]:
        st.warning("Supervisor approval required before this case can move.")

    st.markdown("##### Mandatory checks")
    for ch in case["checks"]:
        confidence = ch.get("confidence")
        confidence_text = f"(confidence {confidence:.2f})" if confidence is not None else ""
        st.markdown(
            f"{_badge(ch['status'])} &nbsp; **{html.escape(ch['checker_name'])}** "
            f"<span style='color:#6B7280'>{confidence_text}</span><br>"
            f"<span style='margin-left:4px'>{html.escape(ch['detail'])}</span><br>"
            f"<span style='color:#6B7280'>Evidence: {html.escape(', '.join(ch.get('evidence_ids', [])))}</span>",
            unsafe_allow_html=True)

    approval = case.get("approval")
    if approval:
        st.markdown("##### Approval records")
        st.markdown(f"- {html.escape(approval['timestamp'])}: **{html.escape(approval['action'])}** "
                    f"by {html.escape(approval['operator_id'])} (evidence v{approval['evidence_version']}): "
                    f"{html.escape(approval['reason'])}")
