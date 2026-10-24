import html
import pydeck as pdk
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


def _escape_audit_text(value):
    if isinstance(value, str):
        return html.escape(_redact_digits(value))
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


def render_case_panel(case, audit=None, geofence=None, geofence_error=None):
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
        st.caption(f"Handoff note · {html.escape(_redact_digits(event['notes']))}")
    investigations = [
        item.get("payload", {}) for item in (audit or {}).get("events", [])
        if str(item.get("kind", "")).upper() == "AGENT_INVESTIGATION"
    ]
    mode = st.segmented_control(
        "Decision view", ["Rules decision", "Rules + agent"], default="Rules + agent",
        key=f"decision_view_{case['shipment_id']}", label_visibility="collapsed",
    )
    st.markdown(f"**:material/psychology: {html.escape(decision['recommended_action'])}**")
    st.caption(html.escape(decision["reason"]))
    if decision.get("unresolved_checks"):
        names = ", ".join(html.escape(x.replace("_", " ")) for x in decision["unresolved_checks"])
        st.caption(f"Unresolved · {names}")
    if decision.get("requires_human_approval"):
        st.warning("Supervisor approval required before this case can move.")
    if mode == "Rules + agent" and investigations:
        latest = investigations[-1]
        st.caption("Advisory investigation · does not change the rules decision")
        call = latest.get("tool_call")
        if call:
            safe_call = _escape_audit_text(call)
            st.markdown(f"**Tool:** {html.escape(str(safe_call.get('tool_name', 'Lookup')))}")
            if safe_call.get("arguments"):
                arguments = ", ".join(f"{key}={value}" for key, value in safe_call["arguments"].items())
                st.caption(f"Arguments · {html.escape(arguments)}")
            if safe_call.get("result_summary"):
                st.write(html.escape(str(safe_call["result_summary"])))
            if safe_call.get("evidence_ids"):
                evidence = ", ".join(html.escape(str(value)) for value in safe_call["evidence_ids"])
                st.caption(f"Tool evidence · {evidence}")
        elif latest.get("reasoning"):
            st.write(html.escape(str(latest["reasoning"])))

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

    location = event.get("location") or {}
    checkpoints = (geofence or {}).get("checkpoints", [])
    map_rows = []
    if location.get("latitude") is not None and location.get("longitude") is not None:
        map_rows.append({"latitude": location["latitude"], "longitude": location["longitude"],
                         "radius_m": 12, "label": "Reported handoff"})
    for index, checkpoint in enumerate(checkpoints, start=1):
        if checkpoint.get("latitude") is not None and checkpoint.get("longitude") is not None:
            map_rows.append({"latitude": checkpoint["latitude"], "longitude": checkpoint["longitude"],
                             "radius_m": max(1, float(checkpoint.get("radius_m") or 30)),
                             "label": str(checkpoint.get("checkpoint_id") or f"Checkpoint {index}")})
    if map_rows or geofence_error:
        with st.expander("Handoff location and geofence", expanded=bool(checkpoints)):
            if map_rows:
                center = map_rows[0]
                layer = pdk.Layer(
                    "ScatterplotLayer", data=map_rows[:1], get_position="[longitude, latitude]",
                    get_radius="radius_m", radius_units="meters", get_fill_color="[255, 75, 75, 155]",
                    get_line_color="[255, 255, 255, 210]", line_width_min_pixels=1,
                    pickable=True, auto_highlight=True,
                )
                checkpoint_layer = pdk.Layer(
                    "ScatterplotLayer", data=map_rows[1:], get_position="[longitude, latitude]",
                    get_radius="radius_m", radius_units="meters", get_fill_color="[94, 224, 188, 65]",
                    get_line_color="[94, 224, 188, 230]", line_width_min_pixels=2,
                    pickable=True, auto_highlight=True,
                )
                st.pydeck_chart(
                    pdk.Deck(
                        layers=[checkpoint_layer, layer],
                        initial_view_state=pdk.ViewState(latitude=center["latitude"], longitude=center["longitude"],
                                                         zoom=15, pitch=0),
                        tooltip={"text": "{label} · radius {radius_m} m"},
                        map_style="dark",
                    ),
                    height=260,
                    use_container_width=True,
                )
                st.caption("Red circle: reported handoff marker. Green circles: authorized checkpoint boundaries at their configured radii.")
            if geofence_error:
                st.caption(f"Checkpoint geofence unavailable: {html.escape(str(geofence_error))}")
            if not checkpoints and not geofence_error:
                st.caption("No matching authorized checkpoint was returned for this case.")


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

    events = audit.get("events", [])
    if events:
        st.markdown("**Case lifecycle**")
        with st.container(horizontal=True, wrap=True, gap="small"):
            for event in events:
                kind = str(event.get("kind", "AUDIT")).replace("_", " ").title()
                st.badge(kind, icon=":material/check_circle:", color="green")
        with st.expander("Hash-chain entries", expanded=False):
            rows = [{
                "Seq": event.get("seq"),
                "Event": event.get("kind"),
                "Recorded": event.get("recorded_at"),
                "Previous hash": str(event.get("prev_hash") or "GENESIS")[:18],
                "Entry hash": str(event.get("entry_hash") or "")[:18],
                "MAC": str(event.get("mac") or "")[:18],
            } for event in events]
            st.dataframe(rows, hide_index=True, width="stretch",
                         height=min(260, 42 + 36 * len(rows)),
                         alt="Audit event sequence and hash-chain links")
            st.caption("Hash prefixes are shown for readability; chain validity is reported above.")

    investigations = [event for event in events
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
