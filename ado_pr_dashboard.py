"""Backward-compatible Streamlit Cloud entry point for the ADO dashboard."""

import runpy
from pathlib import Path


app_path = Path(__file__).parent / "Projects" / "ADO_PR_Dashboard" / "ado_pr_dashboard.py"
runpy.run_path(str(app_path), run_name="__main__")
