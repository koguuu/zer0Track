import streamlit as st
import zt_data as data


def render_approval_form(case, operator):
    """Supervisor can approve, hold or reject with a comment. Cannot bypass a failed mandatory check."""
    st.markdown("##### Supervisor decision")
    if case["state"].upper() != "PENDING":
        st.info("This case is no longer pending, so the backend will not accept another approval.")
        return
    failed = any(c["status"] == "FAIL" for c in case["checks"])
    options = ["hold", "reject"] if failed else ["accept", "hold", "reject", "escalate"]
    if failed:
        st.caption("A mandatory check failed, so this case cannot be approved.")

    with st.form(f"approval_{case['shipment_id']}", clear_on_submit=True):
        decision = st.radio("Decision", options, horizontal=True, format_func=str.title)
        comment = st.text_area("Reason (required)")
        if st.form_submit_button("Save decision"):
            if not comment.strip():
                st.error("Add a comment before saving.")
            else:
                try:
                    data.save_approval(case, decision, comment.strip(), operator)
                    st.success(f"{decision} saved for {case['shipment_id']}.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not save. Check the API is running. Details: {e}")
