import streamlit as st
import zt_data as data
from views.case_list import render_case_list
from views.timeline import render_timeline
from views.approval import render_approval_form

st.set_page_config(page_title="zer0Track", page_icon="📦", layout="wide")
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&display=swap');
html, body, [class*="css"] { font-family: 'IBM Plex Sans', sans-serif; }
h1, h2, h3, h5 { color: #1F2933; }
</style>""", unsafe_allow_html=True)

with st.sidebar:
    st.title("zer0Track")
    operator = st.text_input("Supervisor ID", value=data.OPERATOR_ID or "Supervisor 1")
    st.caption("Data: " + ("mock cases" if data.USE_MOCK else f"API at {data.API_BASE}"))

try:
    cases = data.get_cases()
except Exception as e:
    st.error(f"Could not load cases. Check the API is running at {data.API_BASE}. Details: {e}")
    st.stop()

st.title("Handoff review")
cols = st.columns(4)
for col, s in zip(cols, ["pending", "flagged", "escalated", "accepted"]):
    col.metric(s.capitalize(), sum(c["status"] == s for c in cases))

left, right = st.columns([1, 1.2], gap="large")
with left:
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
        st.info("Select a case on the left to see its checks and timeline.")
