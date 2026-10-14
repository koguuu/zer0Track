import html
import streamlit as st

BADGE_COLORS = {"PASS": "green", "FAIL": "red", "UNKNOWN": "orange"}


def _escape_audit_text(value):
    if isinstance(value, str):
        return html.escape(value)
    if isinstance(value, dict):
        return {key: _escape_audit_text(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_escape_audit_text(child) for child in value]
    return value


def _find_payload_fields(value, keys):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in keys:
                found.append((key, child))
            found.extend(_find_payload_fields(child, keys))
    elif isinstance(value, list):
        for child in value:
            found.extend(_find_payload_fields(child, keys))
    return found


def render_timeline(case, audit=None, audit_error=None, investigation_result=None):
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

    st.markdown("##### :material/history: Agent and audit trail")
    if investigation_result:
        st.markdown("**Latest investigation response**")
        st.json(_escape_audit_text(investigation_result), expanded=False)
    if audit_error:
        st.error(f"Could not load audit trail: {html.escape(str(audit_error))}")
        return
    if audit is None:
        st.info("Audit trail is unavailable.")
        return

    chain_valid = audit.get("chain_valid")
    if chain_valid is True:
        st.badge("Audit chain valid", icon=":material/verified:", color="green")
    elif chain_valid is False:
        st.badge("Audit chain invalid", icon=":material/gpp_bad:", color="red")
    else:
        st.badge("Audit chain status unavailable", color="gray")

    investigation_events = [
        event for event in audit.get("events", [])
        if str(event.get("kind", "")).upper() == "AGENT_INVESTIGATION"
    ]
    if not investigation_events:
        st.info("Not yet investigated")
        return

    tool_keys = {"tool_call", "tool_results", "tool_calls", "tools", "checker_results"}
    injection_keys = {"injection_signals", "prompt_injection_signals", "injection_findings", "injection_signal"}
    for event in investigation_events:
        payload = event.get("payload", {})
        safe_payload = _escape_audit_text(payload)
        with st.container(border=True):
            st.markdown("**Agent investigation**")
            st.caption(html.escape(str(event.get("recorded_at", "Timestamp unavailable"))))
            if payload.get("reasoning"):
                st.write(html.escape(str(payload["reasoning"])))
            if payload.get("unresolved_check"):
                st.caption(f"Unresolved check · {html.escape(str(payload['unresolved_check']))}")
            questions = payload.get("unresolved_questions", [])
            if questions:
                st.markdown("Open questions")
                for question in questions:
                    st.markdown(f"- {html.escape(str(question))}")
            if isinstance(payload.get("suggests_resolved"), bool):
                st.caption("Agent suggests resolved: " + ("Yes" if payload["suggests_resolved"] else "No"))
            tool_sections = _find_payload_fields(safe_payload, tool_keys)
            injection_sections = _find_payload_fields(safe_payload, injection_keys)
            if tool_sections:
                st.markdown("Agent tool results")
                for label, result in tool_sections:
                    st.caption(html.escape(str(label)))
                    st.json(result, expanded=False)
            if injection_sections:
                st.markdown("Injection signals")
                for label, signals in injection_sections:
                    st.caption(html.escape(str(label)))
                    st.json(signals, expanded=False)
            if not tool_sections:
                st.caption("No structured tool-results field was found in this event.")
            if not injection_sections:
                st.caption("No structured injection-signals field was found in this event.")
            with st.expander("Full investigation payload"):
                st.json(safe_payload, expanded=True)


def render_case_panel(case):
    """Compact case summary for the focused review drawer."""
    event = case["event"]
    decision = case["decision"]
    state = case.get("state", "unknown").replace("_", " ").title()
    heading, status = st.columns([3, 1], vertical_alignment="center")
    with heading:
        st.subheader(html.escape(case["shipment_id"]), icon=":material/package_2:")
    with status:
        st.badge(state, color={"Pending": "orange", "Accepted": "green", "Flagged": "red",
                               "Held": "red", "Escalated": "violet", "Rejected": "gray"}.get(state, "gray"))
    handoff = event.get("handoff_type", "handoff").replace("_", " ").title()
    st.caption(f"{html.escape(handoff)} handoff · Handler {html.escape(event['handler_id'])} · Case v{case['version']}")
    if event.get("notes"):
        st.caption(f"Handoff note · {html.escape(event['notes'])}")
    st.markdown(f"**:material/psychology: {html.escape(decision['recommended_action'])}**")
    st.caption(html.escape(decision["reason"]))
    if decision.get("unresolved_checks"):
        names = ", ".join(html.escape(x.replace("_", " ")) for x in decision["unresolved_checks"])
        st.caption(f"Unresolved · {names}")
    if decision.get("requires_human_approval"):
        st.warning("Supervisor approval required before this case can move.")

    st.markdown("**:material/rule: Mandatory checks**")
    checks = case.get("checks", [])
    columns = st.columns(max(1, len(checks)), gap="small")
    for column, check in zip(columns, checks):
        with column.container(border=True):
            name = check["checker_name"].replace("_", " ").title()
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                st.markdown(f"**{html.escape(name)}**")
                st.badge(check["status"], color=BADGE_COLORS.get(check["status"], "gray"))
            st.caption(html.escape(check["detail"]))
            details = []
            if check.get("confidence") is not None:
                details.append(f"Confidence {check['confidence']:.0%}")
            if check.get("evidence_ids"):
                evidence = ", ".join(html.escape(str(value)) for value in check["evidence_ids"])
                details.append(f"Evidence {evidence}")
            if details:
                st.caption(" · ".join(details))

    approval = case.get("approval")
    if approval:
        with st.container(border=True):
            st.markdown("**:material/fact_check: Supervisor record**")
            st.caption(f"{html.escape(str(approval['timestamp']))} · Evidence v{approval['evidence_version']}")
            st.markdown(f"**{html.escape(approval['action'].title())}** by {html.escape(approval['operator_id'])}")
            st.write(html.escape(approval["reason"]))


def render_audit_panel(audit, audit_error=None, investigation_result=None):
    """Compact audit and investigation view for the focused review drawer."""
    st.markdown("**:material/history: Agent and audit trail**")
    if investigation_result:
        st.caption("Latest investigation response")
        st.json(_escape_audit_text(investigation_result), expanded=False)
    if audit_error:
        st.error(f"Could not load audit trail: {html.escape(str(audit_error))}")
        return
    if audit is None:
        st.info("Audit trail is unavailable.")
        return

    chain_valid = audit.get("chain_valid")
    if chain_valid is True:
        st.badge("Audit chain valid", icon=":material/verified:", color="green")
    elif chain_valid is False:
        st.badge("Audit chain invalid", icon=":material/gpp_bad:", color="red")
    else:
        st.badge("Audit chain status unavailable", color="gray")

    investigations = [event for event in audit.get("events", [])
                       if str(event.get("kind", "")).upper() == "AGENT_INVESTIGATION"]
    if not investigations:
        st.caption(":material/info: Not yet investigated")
        return

    tool_keys = {"tool_call", "tool_results", "tool_calls", "tools", "checker_results"}
    injection_keys = {"injection_signals", "prompt_injection_signals", "injection_findings", "injection_signal"}
    for index, event in enumerate(investigations):
        payload = event.get("payload", {})
        safe_payload = _escape_audit_text(payload)
        if index == len(investigations) - 1:
            event_container = st.container(border=True)
        else:
            event_container = st.expander(f"Earlier investigation {index + 1}", expanded=False)
        with event_container:
            st.caption(f"Investigation · {html.escape(str(event.get('recorded_at', 'Timestamp unavailable')))}")
            if payload.get("reasoning"):
                st.write(html.escape(str(payload["reasoning"])))
            if payload.get("unresolved_check"):
                st.caption(f"Unresolved check · {html.escape(str(payload['unresolved_check']))}")
            for question in payload.get("unresolved_questions", []):
                st.markdown(f"- {html.escape(str(question))}")
            if isinstance(payload.get("suggests_resolved"), bool):
                st.caption("Agent suggests resolved: " + ("Yes" if payload["suggests_resolved"] else "No"))
            for label, result in _find_payload_fields(safe_payload, tool_keys):
                st.caption(f"Agent tool result · {html.escape(str(label))}")
                st.json(result, expanded=False)
            for label, signals in _find_payload_fields(safe_payload, injection_keys):
                st.caption(f"Injection signal · {html.escape(str(label))}")
                st.json(signals, expanded=False)
            with st.expander("Full investigation payload", key=f"full_investigation_{index}"):
                st.json(safe_payload, expanded=True)
