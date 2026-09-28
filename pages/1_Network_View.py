"""Network View - Galaxy and Spider Web Visualization"""

# Page config is inherited from app.py

from core_utils import DB_PATH

if not DB_PATH.exists():
    import streamlit as st

    st.info(
        "The network view reads the private `crm.sqlite` database, which is "
        "intentionally not published with this repo. See the Public Data Boundary "
        "section in README.md for what a public clone does and does not include."
    )
    st.stop()

from leads_network import main as network_main

network_main()
