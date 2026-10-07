import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from jira_client import DEFAULT_SPRINT_IDS, ISSUE_COLUMNS, JiraClient, JiraError, main_report, parse_sprint_ids, summarize, validate_url
from presentation import breakdown_chart, donut_chart, quality_chart, quality_counts, status_chart


@st.cache_data(ttl=300, max_entries=64, show_spinner=False)
def load_sprint(url: str, email: str, token: str, sprint_id: int, flag_field: str):
    client = JiraClient(url, email, token)
    try:
        return client.report(sprint_id, flag_field)
    finally:
        client.close()


st.set_page_config(page_title="Jira sprint dashboard", page_icon=":material/view_kanban:", layout="wide")
st.html("""
<style>
.stApp {background: linear-gradient(135deg, #edf3ff 0%, #f8fafc 55%, #eef2ff 100%); color: #172554;}
.st-key-jira_hero {background: linear-gradient(115deg, #172554, #1d4ed8 70%, #0369a1);
    border-radius: 20px; padding: 24px 30px; box-shadow: 0 12px 32px #1e40af20;}
.st-key-jira_hero h1, .st-key-jira_hero p {color: #ffffff;}
.st-key-jira_sprint_card {background: #ffffff; border-radius: 16px;}
.st-key-jira_sprint_card [data-testid="stMetric"] {background: #eff6ff; border-radius: 12px;}
.st-key-jira_hero h1 {letter-spacing: -0.03em;}
</style>
""")
with st.container(key="jira_hero"):
    st.title("Jira sprint dashboard")
    st.caption("DELIVERY & QUALITY | Sprint performance, bugs, and defects")
st.caption(
    "Completed counts use Jira's Done status category. Closed sprints show current issue "
    "statuses, not statuses at sprint closure. This is not a historical burndown or velocity report."
)
try:
    url = st.secrets.get("JIRA_URL", "")
    email = st.secrets.get("JIRA_EMAIL", "")
    token = st.secrets.get("JIRA_API_TOKEN", "")
    flag_field = st.secrets.get("JIRA_FLAGGED_FIELD", "")
except StreamlitSecretNotFoundError:
    url, email, token, flag_field = "", "", "", ""

if any(not isinstance(value, str) for value in (url, email, token, flag_field)):
    st.error("Jira secret values must be strings.")
    st.stop()
if not url.strip() or not email.strip() or not token.strip():
    st.error("Configure JIRA_URL, JIRA_EMAIL, and JIRA_API_TOKEN in .streamlit/secrets.toml or deployment secrets.")
    st.stop()
try:
    url = validate_url(url.strip())
except JiraError as error:
    st.error(str(error))
    st.stop()

with st.sidebar:
    st.header("Sprint selection")
    with st.form("sprint_form"):
        ids_text = st.text_area(
            "Sprint IDs",
            value=DEFAULT_SPRINT_IDS,
            help="Replace these IDs each sprint. Commas, spaces, and new lines are supported.",
        )
        submitted = st.form_submit_button("Fetch / refresh report", type="primary")
    st.caption(f"Jira: {url}")
    st.caption("Each fetch refreshes the selected sprints. Filters below do not call Jira again.")
    st.divider()
    st.caption("Dashboard designed by Ramesh N")

config_key = (url, email, token, flag_field, "subtask-schema-v1")
if st.session_state.get("jira_config") != config_key:
    st.session_state["jira_config"] = config_key
    st.session_state.pop("jira_reports", None)
    st.session_state.pop("jira_errors", None)
    st.session_state.pop("jira_requested_ids", None)

if submitted:
    try:
        sprint_ids = parse_sprint_ids(ids_text)
    except JiraError as error:
        st.error(str(error))
        st.stop()
    reports, errors = [], []
    with st.spinner("Fetching sprint metadata and all issue pages..."):
        for sprint_id in sprint_ids:
            try:
                load_sprint.clear(url, email, token, sprint_id, flag_field)
                reports.append(load_sprint(url, email, token, sprint_id, flag_field))
            except JiraError as error:
                errors.append(f"Sprint {sprint_id}: {error}")
    st.session_state["jira_reports"] = reports
    st.session_state["jira_errors"] = errors
    st.session_state["jira_requested_ids"] = sprint_ids

if "jira_reports" not in st.session_state:
    st.info("Enter sprint IDs in the sidebar and select Fetch / refresh report.")
    st.stop()
for error in st.session_state["jira_errors"]:
    st.error(error)
source_reports = st.session_state["jira_reports"]
if not source_reports:
    st.warning("No sprint reports loaded. Resolve the errors and fetch again.")
    st.stop()
reports = [main_report(report) for report in source_reports]
if st.session_state["jira_errors"]:
    st.warning("Partial results: failed sprints are excluded from tables and exports.")
st.caption("Requested sprint IDs: " + ", ".join(map(str, st.session_state["jira_requested_ids"])))
st.caption("Report scope: Story, Bug, and Defect only. Subtasks and other issue types are excluded from the main report and exports.")

comparison = pd.DataFrame([summarize(report) for report in reports])
st.subheader("Sprint delivery overview")
left, right = st.columns(2)
with left, st.container(border=True):
    st.markdown("**Delivery by sprint**")
    st.altair_chart(status_chart(comparison), width="stretch")
with right, st.container(border=True):
    st.markdown("**Bugs and defects by sprint**")
    st.altair_chart(quality_chart(reports), width="stretch")
st.caption("Sprint order follows the submitted IDs. These are current counts, not a historical trend.")
st.subheader("Sprint comparison")
st.dataframe(comparison, hide_index=True, width="stretch")
st.download_button(
    "Download sprint comparison",
    comparison.to_csv(index=False).encode("utf-8"),
    file_name="jira_sprint_comparison.csv",
    mime="text/csv",
)
st.caption(
    "Counts are per sprint membership; an issue carried across sprints can appear more than once. "
    "Remaining includes unknown status categories. Empty sprints have no completion percentage."
)

report_id = st.selectbox(
    "Sprint details",
    [report.sprint_id for report in reports],
    format_func=lambda value: next(f"{report.name} ({value})" for report in reports if report.sprint_id == value),
)
report = next(report for report in reports if report.sprint_id == report_id)
summary = summarize(report)
issues = pd.DataFrame(report.issues, columns=ISSUE_COLUMNS)
quality = quality_counts(issues)
with st.container(border=True, key="jira_sprint_card"):
    st.subheader(report.name)
    st.caption(f"Sprint {report.sprint_id} | State: {report.state} | Fetched: {report.fetched_at}")
    st.write(f"Start: {report.start or 'Not set'} | End: {report.end or 'Not set'} | Closed: {report.completed or 'Not closed'}")
    if report.goal:
        st.text(f"Goal: {report.goal}")
    with st.container(horizontal=True):
        for label in ("Total issues", "To do", "In progress", "Done", "Remaining", "Unassigned"):
            st.metric(label, summary[label], border=True)
        st.metric("Completion", f"{summary['Done (%)']:.1f}%" if summary["Done (%)"] is not None else "N/A", border=True)
    with st.container(horizontal=True):
        for label, value in quality.items():
            st.metric(label, value, border=True)
    st.caption("Quality counts use exact issue types Bug and Defect (case-insensitive). Open means not in the Done category.")
    if summary["Unknown"]:
        st.warning(f"{summary['Unknown']} issues have an unknown status category and are not counted as Done.")
    if not flag_field:
        st.caption("Flagged/blocked counts are unavailable until JIRA_FLAGGED_FIELD is configured.")
    elif any(issue["Flagged"] is None for issue in report.issues):
        st.warning("Flagged field was missing on some issues; the flagged count may be incomplete.")
    else:
        st.metric("Flagged issues", summary["Flagged"])

if issues.empty:
    st.info("This sprint contains no issues of type Story, Bug, or Defect visible to the configured Jira account.")
else:
    st.subheader("Delivery and quality insights")
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.markdown("**Delivery status mix**")
        st.altair_chart(donut_chart(issues), width="stretch")
    with right, st.container(border=True):
        st.markdown("**Issue type distribution**")
        st.altair_chart(breakdown_chart(issues, "Issue type", "#7c3aed"), width="stretch")
    quality_issues = issues[issues["Issue type"].str.strip().str.casefold().isin(["bug", "defect"])]
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.markdown("**Bugs / defects by priority**")
        if quality_issues.empty:
            st.info("No Bug or Defect issue types in this sprint snapshot.")
        else:
            st.altair_chart(breakdown_chart(quality_issues, "Priority", "#e11d48"), width="stretch")
    with right, st.container(border=True):
        st.markdown("**Bugs / defects by status**")
        if quality_issues.empty:
            st.caption("Quality status chart appears when Bug or Defect issues are present.")
        else:
            st.altair_chart(breakdown_chart(quality_issues, "Status", "#d97706"), width="stretch")
    with st.container(border=True):
        st.markdown("**Issues by assignee**")
        st.caption("Issue counts, not effort estimates or individual performance scores.")
        st.altair_chart(breakdown_chart(issues, "Assignee", show_labels=True), width="stretch")
    st.subheader("Issue details")
    left, right = st.columns(2)
    with left:
        statuses = st.multiselect("Filter statuses", sorted(issues["Status"].unique()), key=f"statuses_{report_id}")
    with right:
        assignees = st.multiselect("Filter assignees", sorted(issues["Assignee"].unique()), key=f"assignees_{report_id}")
    types = st.multiselect("Filter issue types", sorted(issues["Issue type"].unique()), key=f"types_{report_id}")
    filtered = issues
    if statuses:
        filtered = filtered[filtered["Status"].isin(statuses)]
    if assignees:
        filtered = filtered[filtered["Assignee"].isin(assignees)]
    if types:
        filtered = filtered[filtered["Issue type"].isin(types)]
    st.caption(f"{len(filtered)} of {len(issues)} in-scope issues shown. Cards and charts describe all Story, Bug, and Defect issues in this sprint.")
    st.dataframe(
        filtered,
        hide_index=True,
        width="stretch",
        column_config={"Jira link": st.column_config.LinkColumn("Jira link")},
    )
    st.download_button(
        "Download displayed issues",
        filtered.to_csv(index=False).encode("utf-8"),
        file_name=f"jira_sprint_{report_id}_issues.csv",
        mime="text/csv",
    )

if st.checkbox("Show subtask details", value=False, key="show_subtasks"):
    source_report = next(report for report in source_reports if report.sprint_id == report_id)
    subtasks = pd.DataFrame(
        [issue for issue in source_report.issues if issue["Is subtask"]],
        columns=ISSUE_COLUMNS,
    )
    st.subheader("Subtask details")
    st.caption("Separate detail view only; these issues do not change the main report counts or charts.")
    if subtasks.empty:
        st.info("No subtasks visible in this sprint.")
    else:
        st.dataframe(
            subtasks,
            hide_index=True,
            width="stretch",
            column_config={"Jira link": st.column_config.LinkColumn("Jira link")},
        )
        st.download_button(
            "Download subtask details",
            subtasks.to_csv(index=False).encode("utf-8"),
            file_name=f"jira_sprint_{report_id}_subtasks.csv",
            mime="text/csv",
        )

st.divider()
st.caption("Dashboard designed by Ramesh N")
