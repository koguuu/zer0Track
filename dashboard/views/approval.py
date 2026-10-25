import html

import streamlit as st
import zt_data as data


def render_approval_form(case, operator, supervisor_token=None, pending_approvals=None):
    """Supervisor can approve, hold or reject with a comment. Cannot bypass a failed mandatory check.

    Escalate/reject require a second, different operator to confirm before
    they take effect -- the backend enforces this; this UI just shows the
    pending state and nudges the right next action. Reject and escalate can
    be pending at the same time, so pending_approvals is a list.
    """
    st.markdown("##### Supervisor decision")
    if case["state"].upper() != "PENDING":
        st.info("This case is no longer pending, so the backend will not accept another approval.")
        return

    pending_approvals = pending_approvals or []
    pending_by_action = {}
    for entry in pending_approvals:
        pending_by_action.setdefault(entry["action"], entry)  # one request per action is enough to show
        same_operator = entry.get("operator_id") == operator
        st.warning(
            f"**{entry['action'].title()}** was requested by "
            f"**{html.escape(str(entry['operator_id']))}** and is waiting on a second, "
            f"different operator to confirm before it takes effect.",
            icon=":material/pending_actions:",
        )
        if same_operator:
            st.caption("You requested this — sign in as a different operator to confirm it.")
        else:
            st.caption(f"Confirm by selecting **{entry['action'].title()}** again below as "
                       f"{html.escape(str(operator))}.")

    can_accept = data.can_accept(case)
    options = ["hold", "reject", "escalate"]
    if can_accept:
        options.insert(0, "accept")
    else:
        st.caption("Accept is available only when all three checks pass and the agent decision is ACCEPT_READY.")

    confirmable = [a for a, e in pending_by_action.items() if e.get("operator_id") != operator and a in options]
    default = confirmable[0] if confirmable else "hold"
    shipment_id = case["shipment_id"]
    with st.form(f"approval_{shipment_id}", clear_on_submit=True):
        decision = st.segmented_control("Decision", options, format_func=str.title, default=default,
                                        key=f"approval_decision_{shipment_id}")
        comment = st.text_area("Reason (required)", height=80, key=f"approval_reason_{shipment_id}")
        pending_for_decision = pending_by_action.get(decision)
        is_confirm = pending_for_decision is not None and pending_for_decision.get("operator_id") != operator
        requested_by_me = pending_for_decision is not None and pending_for_decision.get("operator_id") == operator
        label = ("Confirm decision" if is_confirm else
                 "Request decision" if decision in data.DUAL_APPROVAL_ACTIONS else "Record decision")
        if st.form_submit_button(label, type="primary", icon=":material/check:",
                                 key=f"approval_submit_{shipment_id}"):
            if not comment.strip():
                st.error("Add a comment before saving.")
            elif requested_by_me:
                st.error(f"You already requested '{decision}' on this case — "
                         "sign in as a different operator to confirm it.")
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
