"""
LeadOps - Main Entry Point
Run: streamlit run app.py
"""

import streamlit as st

# Page config must be first Streamlit command
st.set_page_config(
    page_title="LeadOps",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        'About': "# LeadOps Database UI\nQuery and manage your lead database.",
        'Get Help': None,
        'Report a bug': None,
    }
)

# Import and run main dashboard
from leads_ui import main
main()

