from datetime import datetime

import pandas as pd
import streamlit as st
import zt_data as data
from views.case_list import render_case_list
from views.timeline import render_timeline
from views.approval import render_approval_form

st.set_page_config(page_title="zer0Track", page_icon="📦", layout="wide")
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
# A case can be backend-state PENDING (not yet HELD/ESCALATED) while its
# agent decision already says RECOMMEND_HOLD/REQUEST_VERIFICATION -- that
# genuinely needs a supervisor's attention, not just routine triage, so it
# must count here too. Previously only flagged/escalated counted, silently
# undercounting pending cases the agent had already called out.
pending_needs_attention = sum(
    1 for c in cases if c["status"] == "pending" and c["decision"]["decision"] != "ACCEPT_READY"
)
needs_attention = counts["flagged"] + counts["escalated"] + pending_needs_attention
metric_cols = st.columns(4)
metric_cols[0].metric("Cases in queue", len(cases), icon=":material/inventory_2:", border=True)
metric_cols[1].metric("Pending review", counts["pending"], icon=":material/pending:", border=True)
metric_cols[2].metric("Needs attention", needs_attention, icon=":material/priority_high:", border=True)
metric_cols[3].metric("Accepted", counts["accepted"], icon=":material/task_alt:", border=True)

left, right = st.columns([1, 1.35], gap="large")
with left:
    st.subheader("Queue pulse", icon=":material/monitoring:")
    chart_data = pd.DataFrame({"Cases": list(counts.values())}, index=[s.title() for s in counts])
    st.bar_chart(chart_data, horizontal=True, color="#5ee0bc", height=190,
                 alt="Number of handoff cases in each current status")
    st.subheader("Case queue", icon=":material/inbox:")
    picked = render_case_list(cases)
    if picked:
        st.session_state.selected = picked
with right:
    sid = st.session_state.get("selected")
    if sid:
        case = data.get_case(sid)
        render_timeline(case)
        render_approval_form(case, operator)
    else:
        with st.container(border=True):
            st.markdown("### :material/ads_click: Choose a case to investigate")
            st.caption("Select a shipment from the queue to review its checks, evidence, and decision history.")

st.caption(f"zer0Track operations · {datetime.now().astimezone().strftime('%d %b %Y, %H:%M %Z')}")
