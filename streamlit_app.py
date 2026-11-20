import streamlit as st
import streamlit.components.v1 as components
from server import render_dashboard

st.set_page_config(
    page_title="Strange Crew — Odd Crew & Cargo Radar",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Tighten Streamlit's default padding and match the radar dark theme
st.markdown(
    """
    <style>
      .stApp { background-color: #0b0f14; color: #e6edf3; }
      .block-container { padding: 0.6rem 1rem 0.5rem 1rem; max-width: 100%; }
      header[data-testid="stHeader"] { display: none; }
    </style>
    """,
    unsafe_allow_html=True,
)

if "filter_cat" not in st.session_state:
    st.session_state.filter_cat = "all"
if "search_q" not in st.session_state:
    st.session_state.search_q = ""
if "force_refresh" not in st.session_state:
    st.session_state.force_refresh = False

# Top control bar (replaces relative <a href="/?..."> and <form action="/"> navigation)
c1, c2, c3, c4 = st.columns([2.2, 3.2, 3.6, 1.0])

with c1:
    st.session_state.filter_cat = st.radio(
        "Category",
        options=["all", "cargo", "crew"],
        format_func=str.upper,
        horizontal=True,
        label_visibility="collapsed",
        index=["all", "cargo", "crew"].index(st.session_state.filter_cat),
    )

with c2:
    st.session_state.search_q = st.text_input(
        "Search",
        value=st.session_state.search_q,
        placeholder="Search callsign (e.g. UAL123), tail, range, payload...",
        label_visibility="collapsed",
    )

with c3:
    chip_cols = st.columns(6)
    chips = [
        ("RESET", ""),
        ("SUA", "SUA"),
        ("AAR", "REFUELING"),
        ("ORBIT", "orbit"),
        ("OUTSIZE", "outsize"),
        ("PIA", "PIA"),
    ]
    for col, (label, val) in zip(chip_cols, chips):
        if col.button(label, use_container_width=True):
            st.session_state.search_q = val
            st.rerun()

with c4:
    if st.button("↻ REFRESH", use_container_width=True):
        st.session_state.force_refresh = True
        st.rerun()


@st.fragment(run_every=20)
def render_live_radar() -> None:
    force = st.session_state.force_refresh
    st.session_state.force_refresh = False

    html_doc = render_dashboard(
        category=st.session_state.filter_cat,
        query=st.session_state.search_q,
        selected_id="",
        watched=set(),
        force_refresh=force,
    )
    components.html(html_doc, height=860, scrolling=False)


render_live_radar()