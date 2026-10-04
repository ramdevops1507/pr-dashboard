# Jira Dashboard

Reserved for the Jira dashboard. No application is implemented yet.

When implemented, keep the Streamlit entry point (`streamlit_app.py`) and
project-specific `requirements.txt` in this folder. Run it from the repository
root to use the shared Streamlit configuration:

```bash
pip install -r Projects/Jira/requirements.txt
streamlit run Projects/Jira/streamlit_app.py
```

These commands will be available once the application and dependency file exist.
Store credentials in local Streamlit secrets or deployment secrets, never in
source code.
