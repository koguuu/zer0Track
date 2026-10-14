from datetime import datetime
import html

import pandas as pd
import streamlit as st
import zt_data as data
from views.case_list import render_case_list
from views.timeline import render_case_panel, render_audit_panel
from views.approval import render_approval_form

st.set_page_config(page_title="zer0Track", page_icon=":material/inventory_2:", layout="wide")


def _close_case_dialog():
    st.session_state.case_dialog_open = False


@st.dialog("Case review", width="large", position="center", on_dismiss=_close_case_dialog)
def _show_case_dialog(shipment_id, operator):
    case = data.get_case(shipment_id)
    case_panel, review_panel = st.columns([1.15, 1], gap="large")
    with case_panel:
        render_case_panel(case)
    with review_panel:
        if st.button("Run agent investigation", icon=":material/psychology:", key=f"investigate_{shipment_id}"):
            try:
                with st.spinner("Investigating case…"):
                    result = data.investigate_case(shipment_id)
                st.session_state[f"investigation_result_{shipment_id}"] = result.get("investigation", result)
            except Exception as error:
                st.error(f"Investigation failed: {error}")
        render_approval_form(case, operator)
        try:
            audit = data.get_audit_trail(shipment_id)
            audit_error = None
        except Exception as error:
            audit = None
            audit_error = error
        render_audit_panel(audit, audit_error, st.session_state.get(f"investigation_result_{shipment_id}"))

st.markdown("""<style>
</style>""", unsafe_allow_html=True)

st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
:root { --zt-ink: #e7edf5; --zt-muted: #93a4b8; --zt-mint: #5ee0bc; }
.stApp { background: radial-gradient(ellipse at 8% 0%, rgba(40,117,113,.20), transparent 35%), radial-gradient(ellipse at 100% 8%, rgba(61,84,143,.17), transparent 34%), #0b111b; color: var(--zt-ink); font-family: 'DM Sans', sans-serif; }
[data-testid="stHeader"] { background: rgba(11,17,27,.72); }
[data-testid="stSidebar"] { background: linear-gradient(180deg,#101a28 0%,#0b111b 100%); border-right: 1px solid #1e2a39; }
.block-container { max-width: 1500px; padding-top: 2.2rem; padding-bottom: 3rem; }
h1,h2,h3 { font-family: 'Space Grotesk',sans-serif; letter-spacing: -.035em; color: #f2f6fb; }
[data-testid="stMetric"] { background: linear-gradient(145deg,rgba(24,37,52,.95),rgba(16,25,37,.92)); border: 1px solid #263749; border-radius: 16px; padding: 17px 19px; }
[data-testid="stMetricLabel"] { color: #9fb0c4; font-size: .82rem; }
[data-testid="stMetricValue"] { color: #f3f7fb; font-family: 'Space Grotesk',sans-serif; }
[data-testid="stVerticalBlockBorderWrapper"] { border-color: #253548; border-radius: 16px; background: rgba(17,27,40,.48); }
[data-testid="stDataFrame"] { border: 1px solid #263749; border-radius: 13px; overflow: hidden; }
[data-testid="stTextInput"] input,[data-testid="stSelectbox"] [data-baseweb="select"] > div { background: #111d2a; }
div.stButton > button,div[data-testid="stFormSubmitButton"] button { border-radius: 10px; font-weight: 650; }
div[data-testid="stFormSubmitButton"] button[kind="primary"] { background: #56d5b3; border-color: #56d5b3; color: #081511; }
[data-testid="stCaptionContainer"] { color: #91a2b6; }
</style>""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### :material/local_shipping: zer0Track")
    st.caption("HANDOFF TRUST · OPERATIONS")
    st.space("small")
    operator = st.text_input("Supervisor ID", value=data.OPERATOR_ID or "Supervisor 1")
    if data.USE_MOCK:
        st.badge("Demo data", icon=":material/science:", color="orange")
    else:
        st.badge("Live API", icon=":material/cloud_done:", color="green")
        st.caption(data.API_BASE)

try:
    cases = data.get_cases()
except Exception as e:
    st.error(f"Could not load cases. Check the API is running at {data.API_BASE}. Details: {e}")
    st.stop()

with st.container(border=True):
    lead, mode = st.columns([4, 1], vertical_alignment="center")
    with lead:
        st.caption("ZER0TRACK  /  OPERATIONS CENTER")
        st.title("Handoff control room", icon=":material/space_dashboard:")
        st.caption("Review custody evidence, investigate exceptions, and record supervisor decisions.")
    with mode:
        st.caption("DATA SOURCE")
        st.badge("MOCK MODE" if data.USE_MOCK else "CONNECTED", color="orange" if data.USE_MOCK else "green")

counts = {status: sum(c["status"] == status for c in cases) for status in
          ["pending", "flagged", "escalated", "accepted", "rejected"]}
attention_cases = [
    case for case in cases
    if case["status"] in {"flagged", "escalated"}
    or (
        case["status"] == "pending"
        and (
            bool(case.get("decision", {}).get("unresolved_checks"))
            or any(check.get("status") in {"UNKNOWN", "FAIL"} for check in case.get("checks", []))
        )
    )
]
needs_attention = len(attention_cases)
metric_cols = st.columns(4)
metric_cols[0].metric("Cases in queue", len(cases), icon=":material/inventory_2:", border=True)
metric_cols[1].metric("Pending review", counts["pending"], icon=":material/pending:", border=True)
metric_cols[2].metric("Needs attention", needs_attention, icon=":material/priority_high:", border=True)
metric_cols[3].metric("Accepted", counts["accepted"], icon=":material/task_alt:", border=True)

if needs_attention:
    with st.container(horizontal=True, vertical_alignment="center", wrap=True, gap="small"):
        st.badge(f"{needs_attention} need review", icon=":material/priority_high:", color="orange")
        for case in attention_cases:
            if st.button(
                f"{case['shipment_id']} · {case['status'].title()}",
                icon=":material/open_in_new:",
                key=f"priority_case_{case['shipment_id']}",
            ):
                st.session_state.case_to_open = case["shipment_id"]

left, right = st.columns([1, 1.35], gap="large")
with left:
    st.subheader("Queue pulse", icon=":material/monitoring:")
    chart_data = pd.DataFrame({"Cases": list(counts.values())}, index=[s.title() for s in counts])
    st.bar_chart(chart_data, horizontal=True, color="#5ee0bc", height=190,
                 alt="Number of handoff cases in each current status")
    st.subheader("Case queue", icon=":material/inbox:")
    picked = render_case_list(cases)

priority_selection = st.session_state.pop("case_to_open", None)
if priority_selection:
    st.session_state.selected = priority_selection
    st.session_state.case_dialog_open = True
elif picked and picked != st.session_state.get("selected"):
    st.session_state.selected = picked
    st.session_state.case_dialog_open = True

selected = st.session_state.get("selected")
with right:
    if selected:
        case = data.get_case(selected)
        state = case.get("state", "unknown").replace("_", " ").title()
        with st.container(border=True):
            st.subheader(selected, icon=":material/package_2:")
            st.badge(state, color={"Pending": "orange", "Accepted": "green", "Flagged": "red",
                                   "Held": "red", "Escalated": "violet", "Rejected": "gray"}.get(state, "gray"))
            st.markdown(f"**{case['decision']['recommended_action']}**")
            st.caption(html.escape(case["decision"]["reason"]))
            if st.button("Open full case review", icon=":material/open_in_new:",
                          key="open_selected_case"):
                st.session_state.case_dialog_open = True
    else:
        with st.container(border=True):
            st.markdown("### :material/ads_click: Choose a case to investigate")
            st.caption("Select a shipment from the queue to review its checks, evidence, and decision history.")

if st.session_state.get("case_dialog_open") and selected:
    _show_case_dialog(selected, operator)

st.caption(f"zer0Track operations · {datetime.now().astimezone().strftime('%d %b %Y, %H:%M %Z')}")
