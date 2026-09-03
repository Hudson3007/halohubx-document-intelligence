"""
HaloHubX Smart Office AI - Combined App

Three pages, switched via the sidebar:
  1. Dashboard (Company Brain) — home screen, ask questions across your
     saved documents.
  2. Upload Document — preview, extract, and save documents into
     Company Brain.
  3. Review Queue — human-in-the-loop review of documents the halohubx-api
     backend flagged as low-confidence, before they're pushed to an ERP.
     (Requires the separate halohubx-api backend to be running somewhere
     and a partner API key from it — see that project's README.)

Run with:  streamlit run app.py
"""

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv, set_key

from modules import company_brain, document_reader, review_queue, theme
from modules.ai_client import get_client

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(APP_DIR, ".env")

# Create .env if it doesn't exist yet, then load whatever keys are saved in it.
if not os.path.exists(ENV_PATH):
    open(ENV_PATH, "a").close()
load_dotenv(ENV_PATH, override=True)

st.set_page_config(page_title="HalohubX | Smart Office AI", page_icon="🔷", layout="wide")
theme.apply()
st.session_state.setdefault("page", "dashboard")


def _get_saved_key(env_var_name: str) -> str:
    """Check Streamlit Cloud secrets first (used when deployed), then fall
    back to the local .env file (used when running on your own computer)."""
    try:
        if env_var_name in st.secrets:
            return st.secrets[env_var_name]
    except Exception:
        pass  # no secrets.toml at all (e.g. running locally) — that's fine
    return os.environ.get(env_var_name, "")


def _save_key_if_changed(env_var_name: str, value: str, previous: str):
    if value and value != previous:
        try:
            set_key(ENV_PATH, env_var_name, value)
            os.environ[env_var_name] = value
        except Exception:
            pass  # e.g. read-only filesystem on some hosts — value still works this session


with st.sidebar:
    st.markdown("## ⚙️ Settings")

    # --- AI provider (used by Document Reader + Company Brain) ---
    st.markdown("#### AI Provider")
    provider_label = st.radio(
        "AI Provider",
        ["Claude (paid)", "Gemini (free tier)"],
        label_visibility="collapsed",
        help="Switch this any time — e.g. use Gemini's free tier now, "
        "move to Claude once you're billing clients.",
    )
    provider = "gemini" if provider_label.startswith("Gemini") else "claude"
    env_var_name = "GEMINI_API_KEY" if provider == "gemini" else "ANTHROPIC_API_KEY"

    default_key = _get_saved_key(env_var_name)
    key_help = (
        "Free key: https://aistudio.google.com/apikey (no card required)"
        if provider == "gemini"
        else "Get one at https://console.anthropic.com/"
    )
    api_key = st.text_input(
        f"{provider_label.split(' ')[0]} API Key",
        value=default_key,
        type="password",
        help=key_help,
    )
    _save_key_if_changed(env_var_name, api_key, default_key)

    st.caption(
        "Locally: saved in a `.env` file next to app.py. "
        "On Streamlit Cloud: add it under your app's Secrets instead."
    )
    st.divider()

    # --- Backend connection (used only by Review Queue) ---
    st.markdown("#### Review Queue Backend")
    default_backend_url = _get_saved_key("HALOHUBX_BACKEND_URL") or "http://127.0.0.1:8000"
    backend_url = st.text_input(
        "Backend API URL",
        value=default_backend_url,
        help="Where your halohubx-api server is running, e.g. http://127.0.0.1:8000 locally.",
    )
    _save_key_if_changed("HALOHUBX_BACKEND_URL", backend_url, default_backend_url)

    default_partner_key = _get_saved_key("HALOHUBX_PARTNER_API_KEY")
    partner_api_key = st.text_input(
        "Partner API Key",
        value=default_partner_key,
        type="password",
        help="From halohubx-api's create_partner.py — looks like hhx_....",
    )
    _save_key_if_changed("HALOHUBX_PARTNER_API_KEY", partner_api_key, default_partner_key)

    st.divider()

    # --- Navigation ---
    st.markdown("#### Navigate")
    nav_cols_map = {
        "dashboard": "🏠 Dashboard",
        "upload": "⬆️ Upload Document",
        "review": "🔍 Review Queue",
    }
    for page_key, label in nav_cols_map.items():
        is_current = st.session_state["page"] == page_key
        if st.button(
            label,
            type="primary" if is_current else "secondary",
            use_container_width=True,
            disabled=is_current,
            key=f"nav_{page_key}",
        ):
            st.session_state["page"] = page_key
            st.rerun()

    st.divider()
    st.caption("HalohubX Smart Office AI — internal build")

client = get_client(provider, api_key)

PAGE_TITLES = {
    "dashboard": ("🔷 HalohubX — Company Brain", "Ask anything across your saved documents, instantly."),
    "upload": ("🔷 HalohubX — Document Reader", "Upload, preview, and extract key data from any document."),
    "review": ("🔷 HalohubX — Review Queue", "Approve or correct anything the AI flagged before it reaches your ERP."),
}

title, subtitle = PAGE_TITLES[st.session_state["page"]]
theme.hero(title, subtitle)

if st.session_state["page"] == "dashboard":
    company_brain.render(client)
elif st.session_state["page"] == "upload":
    document_reader.render(client)
else:
    review_queue.render(backend_url, partner_api_key)
