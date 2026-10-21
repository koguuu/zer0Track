import html
import re

import streamlit as st

import zt_data as data

BADGE_COLORS = {"PASS": "green", "FAIL": "red", "UNKNOWN": "orange"}

# Mirrors agent/redaction.py's rule exactly (4+ consecutive digits -> same-
# length asterisks). Duplicated here on purpose: the dashboard is a thin
# API client that doesn't import the agent package, so this is a small,
# self-contained copy rather than a cross-package import. Keep both in
# sync if the masking rule changes -- see agent/redaction.py's docstring
# for the full rationale (point-of-use redaction, raw evidence stays
# intact in storage).
_DIGIT_RUN = re.compile(r"\d{4,}")


def _redact_digits(text):
    if text is None:
        return None
    return _DIGIT_RUN.sub(lambda m: "*" * len(m.group()), text)
AUDIT_KIND_ICONS = {
    "EVALUATION": ":material/fact_check:",
    "RECHECK": ":material/sync:",
    "AGENT_INVESTIGATION": ":material/psychology:",
    "APPROVAL": ":material/how_to_reg:",
}


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
        st.caption(f"Handoff note · {html.escape(_redact_digits(event['notes']))}")
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

    st.markdown("##### :material/history: Investigation & audit trail")
    try:
        audit = data.get_audit(case["shipment_id"])
    except Exception as e:
        st.caption(f"Could not load audit trail. Details: {e}")
    else:
        chain_label = "Chain verified" if audit.get("chain_valid") else "CHAIN INVALID — tampering suspected"
        st.badge(chain_label, color="green" if audit.get("chain_valid") else "red",
                 icon=":material/verified:" if audit.get("chain_valid") else ":material/gpp_bad:")
        events = audit.get("events", [])
        if not events:
            st.caption("No audit entries yet for this case.")
        for event in reversed(events):
            kind = event.get("kind", "EVENT")
            icon = AUDIT_KIND_ICONS.get(kind, ":material/receipt_long:")
            with st.container(border=True):
                head, when = st.columns([2, 1])
                head.markdown(f"**{icon} {html.escape(kind.replace('_', ' ').title())}**")
                when.caption(html.escape(str(event.get("recorded_at", ""))))
                payload = event.get("payload", {})
                if kind == "AGENT_INVESTIGATION":
                    unresolved = payload.get("unresolved_check")
                    if unresolved:
                        st.caption(f"Investigated: {html.escape(str(unresolved).replace('_', ' '))}")
                    tool_call = payload.get("tool_call")
                    if tool_call:
                        st.caption(f"Tool: {html.escape(str(tool_call.get('name', '')))} "
                                   f"→ {html.escape(str(tool_call.get('status', '')))}")
                    reasoning = payload.get("reasoning")
                    if reasoning:
                        st.write(html.escape(str(reasoning)))
                    if payload.get("suggests_resolved"):
                        st.caption("Agent suggests this check may now be resolvable — not authoritative; "
                                   "only an authenticated /recheck can confirm it.")
                    injection = payload.get("injection_signal")
                    if injection:
                        flag = "FLAGGED" if injection.get("flagged") else "clean"
                        st.caption(f"Advisory LLM signal: {flag} ({injection.get('category', 'n/a')}) "
                                   "— informational only, never affects the decision above.")
                elif kind in ("EVALUATION", "RECHECK"):
                    decision = payload.get("policy_decision", {})
                    if decision:
                        st.caption(f"Decision: {html.escape(str(decision.get('decision', '')))} — "
                                   f"{html.escape(str(decision.get('reason', '')))}")
                elif kind == "APPROVAL":
                    st.caption(f"{html.escape(str(payload.get('action', '')))} by "
                               f"{html.escape(str(payload.get('operator_id', '')))}: "
                               f"{html.escape(str(payload.get('reason', '')))}")
