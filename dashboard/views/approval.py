import streamlit as st
import zt_data as data


def render_approval_form(case, operator, supervisor_token=None):
    """Supervisor can approve, hold or reject with a comment. Cannot bypass a failed mandatory check."""
    st.markdown("##### Supervisor decision")
    if case["state"].upper() != "PENDING":
        st.info("This case is no longer pending, so the backend will not accept another approval.")
        return
    can_accept = data.can_accept(case)
    options = ["hold", "reject", "escalate"]
    if can_accept:
        options.insert(0, "accept")
    else:
        st.caption("Accept is available only when all three checks pass and the agent decision is ACCEPT_READY.")

    with st.form(f"approval_{case['shipment_id']}", clear_on_submit=True):
        decision = st.segmented_control("Decision", options, format_func=str.title, default="hold")
        comment = st.text_area("Reason (required)", height=80)
        if st.form_submit_button("Record decision", type="primary", icon=":material/check:"):
            if not comment.strip():
                st.error("Add a comment before saving.")
            else:
                try:
                    data.save_approval(case, decision, comment.strip(), operator, supervisor_token)
                    st.success(f"{decision} saved for {case['shipment_id']}.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not save. Check the API is running. Details: {e}")
