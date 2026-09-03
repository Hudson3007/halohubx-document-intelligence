"""
HalohubX visual theme. Custom CSS layered on top of the base color theme
set in .streamlit/config.toml. Call theme.apply() once near the top of
app.py, and theme.hero(...) in place of a plain st.title() for the
branded banner look.
"""

import streamlit as st

# --- HalohubX brand palette ---
WHITE = "#FCFCFD"
LIGHT_BG = "#EEF4F8"
LIGHT_BLUE = "#D7E6F2"
MID_BLUE = "#97B7CF"
SLATE = "#547A95"
NAVY = "#21384C"
INK = "#0B1018"


def apply():
    st.markdown(
        f"""
        <style>
        /* ---------- App background & base typography ---------- */
        .stApp {{
            background-color: {WHITE};
        }}
        h1, h2, h3, h4 {{
            color: {NAVY} !important;
            font-weight: 700 !important;
        }}

        /* ---------- Hero header banner ---------- */
        .dx-hero {{
            background: linear-gradient(135deg, {NAVY} 0%, {SLATE} 100%);
            padding: 26px 30px;
            border-radius: 14px;
            margin-bottom: 22px;
            box-shadow: 0 4px 18px rgba(11, 16, 24, 0.18);
        }}
        .dx-hero h1 {{
            color: {WHITE} !important;
            margin: 0 !important;
            font-size: 1.8rem !important;
        }}
        .dx-hero p {{
            color: {LIGHT_BLUE} !important;
            margin: 6px 0 0 0 !important;
            font-size: 0.95rem;
        }}

        /* ---------- Sidebar ---------- */
        [data-testid="stSidebar"] {{
            background-color: {NAVY};
        }}
        [data-testid="stSidebar"] * {{
            color: {WHITE} !important;
        }}
        [data-testid="stSidebar"] .stTextInput > div > div > input {{
            background-color: {INK} !important;
            color: {WHITE} !important;
            border: 1px solid {SLATE} !important;
        }}
        [data-testid="stSidebar"] hr {{
            border-color: {SLATE} !important;
        }}
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {{
            color: {MID_BLUE} !important;
        }}

        /* ---------- Buttons ---------- */
        .stButton > button {{
            border-radius: 8px !important;
            border: 1px solid {SLATE} !important;
            font-weight: 600 !important;
            transition: all 0.15s ease-in-out;
        }}
        .stButton > button[kind="primary"] {{
            background-color: {SLATE} !important;
            color: {WHITE} !important;
            border: none !important;
        }}
        .stButton > button[kind="primary"]:hover {{
            background-color: {NAVY} !important;
        }}
        .stButton > button:not([kind="primary"]):hover {{
            border-color: {NAVY} !important;
            color: {NAVY} !important;
        }}

        /* ---------- Metrics (e.g. Quick Total) ---------- */
        [data-testid="stMetric"] {{
            background-color: {LIGHT_BG};
            border: 1px solid {LIGHT_BLUE};
            border-radius: 12px;
            padding: 16px 18px;
        }}
        [data-testid="stMetricValue"] {{
            color: {NAVY} !important;
        }}
        [data-testid="stMetricLabel"] {{
            color: {SLATE} !important;
        }}

        /* ---------- Expanders ---------- */
        .streamlit-expanderHeader {{
            background-color: {LIGHT_BG} !important;
            border-radius: 8px !important;
            font-weight: 600 !important;
            color: {NAVY} !important;
        }}

        /* ---------- File uploader ---------- */
        [data-testid="stFileUploaderDropzone"] {{
            background-color: {LIGHT_BG} !important;
            border: 2px dashed {MID_BLUE} !important;
            border-radius: 12px !important;
        }}

        /* ---------- Text inputs / select boxes ---------- */
        .stTextInput > div > div > input {{
            border-radius: 8px !important;
            border: 1px solid {LIGHT_BLUE} !important;
        }}
        .stSelectbox > div > div {{
            border-radius: 8px !important;
            border: 1px solid {LIGHT_BLUE} !important;
        }}

        /* ---------- Dataframes / tables ---------- */
        [data-testid="stDataFrame"] {{
            border: 1px solid {LIGHT_BLUE};
            border-radius: 8px;
            overflow: hidden;
        }}

        /* ---------- Dividers ---------- */
        hr {{
            border-color: {LIGHT_BLUE} !important;
        }}

        /* ---------- Alerts (info/success/warning boxes) ---------- */
        [data-testid="stAlert"] {{
            border-radius: 10px !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def hero(title: str, subtitle: str = ""):
    """Render the branded gradient header banner in place of a plain st.title()."""
    subtitle_html = f"<p>{subtitle}</p>" if subtitle else ""
    st.markdown(
        f"""
        <div class="dx-hero">
            <h1>{title}</h1>
            {subtitle_html}
        </div>
        """,
        unsafe_allow_html=True,
    )
