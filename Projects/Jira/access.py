import hashlib
import hmac
import time

import streamlit as st


def sign_out() -> None:
    st.session_state.clear()


def require_password(password: str) -> None:
    fingerprint = hashlib.sha256(password.encode("utf-8")).hexdigest()
    if st.session_state.get("jira_password_verified") == fingerprint:
        return
    for key in ("jira_password_verified", "jira_reports", "jira_errors", "jira_requested_ids", "jira_config"):
        st.session_state.pop(key, None)
    st.subheader("Sign in to Jira dashboard")
    st.caption("This shared-password screen supplements private Cloud access.")
    waiting = time.monotonic() < st.session_state.get("jira_login_retry_at", 0)
    if waiting:
        st.warning("Please wait a few seconds before trying again, then rerun the page.")
    with st.form("jira_login"):
        st.text_input("Dashboard password", type="password", key="jira_login_password")
        submitted = st.form_submit_button("Sign in", disabled=waiting)
    if submitted and not waiting:
        entered = st.session_state.pop("jira_login_password", "")
        if hmac.compare_digest(entered.encode("utf-8"), password.encode("utf-8")):
            st.session_state["jira_password_verified"] = fingerprint
            st.session_state.pop("jira_login_failures", None)
            st.session_state.pop("jira_login_retry_at", None)
            st.rerun()
        else:
            failures = min(st.session_state.get("jira_login_failures", 0) + 1, 5)
            st.session_state["jira_login_failures"] = failures
            st.session_state["jira_login_retry_at"] = time.monotonic() + min(2 ** failures, 30)
            st.error("Incorrect password. Wait a few seconds before trying again.")
    st.stop()
