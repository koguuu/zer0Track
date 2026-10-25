import html

import streamlit as st
import zt_data as data


def render_approval_form(case, operator, supervisor_token=None, audit=None):
    """Supervisor can approve, hold or reject with a comment. Cannot bypass a failed mandatory check.

    Escalate/reject require a second, different operator to confirm before
    they take effect -- the backend enforces this; this UI just shows the
    pending state and nudges the right next action.
    """
    st.markdown("##### Supervisor decision")
    if case["state"].upper() != "PENDING":
        st.info("This case is no longer pending, so the backend will not accept another approval.")
        return

    pending = (audit or {}).get("pending_dual_approval")
    if pending:
        same_operator = pending.get("requested_by") == operator
        st.warning(
            f"**{pending['action'].title()}** was requested by "
            f"**{html.escape(str(pending['requested_by']))}** and is waiting on a second, "
            f"different operator to confirm before it takes effect.\n\n"
            f"Reason given: {html.escape(str(pending['reason']))}",
            icon=":material/pending_actions:",
        )
        if same_operator:
            st.caption("You requested this — sign in as a different operator to confirm it, "
                       "or submit a different action below to replace your own request.")
        else:
            st.caption(f"Confirm by selecting **{pending['action'].title()}** again below as "
                       f"{html.escape(str(operator))}, or choose a different action to raise an objection.")

    can_accept = data.can_accept(case)
    options = ["hold", "reject", "escalate"]
    if can_accept:
        options.insert(0, "accept")
    else:
        st.caption("Accept is available only when all three checks pass and the agent decision is ACCEPT_READY.")

    default = pending["action"] if pending and not same_operator and pending["action"] in options else "hold"
    with st.form(f"approval_{case['shipment_id']}", clear_on_submit=True):
        decision = st.segmented_control("Decision", options, format_func=str.title, default=default)
        comment = st.text_area("Reason (required)", height=80)
        label = ("Confirm decision" if pending and not same_operator else
                 "Request decision" if decision in data.DUAL_APPROVAL_ACTIONS else "Record decision")
        if st.form_submit_button(label, type="primary", icon=":material/check:"):
            if not comment.strip():
                st.error("Add a comment before saving.")
            else:
                try:
                    result = data.save_approval(case, decision, comment.strip(), operator, supervisor_token)
                    if decision in data.DUAL_APPROVAL_ACTIONS and (result or {}).get("state", "").upper() == "PENDING":
                        st.success(
                            f"{decision.title()} requested for {case['shipment_id']}. "
                            "A different operator must confirm it before it takes effect."
                        )
                    else:
                        st.success(f"{decision.title()} saved for {case['shipment_id']}.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not save. Check the API is running. Details: {e}")
