import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from sonar_client import METRICS, SonarClient, SonarError, format_measure, validate_url


DEFAULT_URL = "https://sonarqube.cep.boots.com"
DEFAULT_PROJECTS = ["BootsApp-iOS", "BootsApp-android"]


@st.cache_data(ttl=300, max_entries=32, show_spinner=False)
def load_project(url: str, token: str, project: str, branch: str):
    client = SonarClient(url, token)
    try:
        return client.snapshot(project, branch, client.available_metrics())
    finally:
        client.close()


st.set_page_config(page_title="Sonar quality dashboard", page_icon=":material/verified:", layout="wide")
st.title("Sonar quality dashboard")
st.caption("Boots mobile apps | Latest analyzed code, not live repository changes")

try:
    token = st.secrets.get("SONAR_TOKEN", "")
    server_url = st.secrets.get("SONAR_URL", DEFAULT_URL)
    projects = st.secrets.get("SONAR_PROJECTS", DEFAULT_PROJECTS)
except StreamlitSecretNotFoundError:
    token, server_url, projects = "", DEFAULT_URL, DEFAULT_PROJECTS

if not isinstance(token, str) or not token.strip():
    st.error("Add SONAR_TOKEN to .streamlit/secrets.toml or your deployment's secrets. Never paste the token into source code.")
    st.stop()
if not isinstance(server_url, str):
    st.error("SONAR_URL must be a string.")
    st.stop()
try:
    server_url = validate_url(server_url)
except SonarError as error:
    st.error(str(error))
    st.stop()
if (
    not isinstance(projects, list)
    or not projects
    or any(not isinstance(project, str) or not project.strip() for project in projects)
):
    st.error("SONAR_PROJECTS must be a non-empty list of project keys.")
    st.stop()
projects = list(dict.fromkeys(project.strip() for project in projects))

with st.sidebar:
    st.header("Projects")
    selected = st.multiselect("Project keys", projects, default=projects)
    branch = st.text_input(
        "Branch (optional)",
        help="Leave blank for each project's main branch. Named branches require SonarQube branch-analysis support.",
    ).strip()
    st.caption(f"Server: {server_url}")
    st.caption("Results are cached for up to 5 minutes. Refresh retrieves a new snapshot.")
    refresh = st.button("Refresh data", type="primary")
    st.divider()
    st.caption("Dashboard designed by Ramesh N")

if refresh:
    for project in selected:
        load_project.clear(server_url, token, project, branch)
if not selected:
    st.info("Select at least one project.")
    st.stop()

snapshots = []
for project in selected:
    with st.spinner(f"Loading {project}..."):
        try:
            snapshot = load_project(server_url, token, project, branch)
        except SonarError as error:
            st.error(f"{project}: {error}")
            continue
    snapshots.append(snapshot)

if not snapshots:
    st.warning("No project data could be loaded. Resolve the errors above, then refresh.")
    st.stop()
if len(snapshots) != len(selected):
    st.warning("Partial results: failed projects are excluded from the comparison and export.")

st.subheader("Project comparison")
rows = [
    {
        "Project": snapshot.name,
        "Project key": snapshot.key,
        "Quality gate": snapshot.gate,
        **{label: snapshot.measures.get(metric) for metric, label in METRICS.items()},
        "Last analysis": snapshot.analysis_date,
        "Fetched at (UTC)": snapshot.fetched_at,
    }
    for snapshot in snapshots
]
comparison = pd.DataFrame(rows)
st.dataframe(comparison, hide_index=True, width="stretch")
st.download_button(
    "Download comparison CSV",
    comparison.to_csv(index=False).encode("utf-8"),
    file_name="sonar_project_comparison.csv",
    mime="text/csv",
)
st.caption("Coverage and duplication are percentages. Technical debt is in minutes. Missing measures are unavailable, not zero.")

for snapshot in snapshots:
    with st.container(border=True):
        st.subheader(snapshot.name)
        st.caption(f"Project key: {snapshot.key} | Branch: {branch or 'Main branch'}")
        if snapshot.gate == "OK":
            st.success("Quality gate: Passed")
        elif snapshot.gate == "ERROR":
            st.error("Quality gate: Failed")
        else:
            st.warning(f"Quality gate: {snapshot.gate} (not a confirmed pass)")
        st.caption(
            f"Last analysis: {snapshot.analysis_date or 'No analysis recorded'} | "
            f"Fetched at: {snapshot.fetched_at}"
        )
        with st.container(horizontal=True):
            for metric, label in METRICS.items():
                st.metric(label, format_measure(metric, snapshot.measures.get(metric)), border=True)
        missing = [label for metric, label in METRICS.items() if metric not in snapshot.measures]
        if missing:
            st.info("Unavailable measures: " + ", ".join(missing))
        if snapshot.conditions:
            st.markdown("**Quality gate conditions**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Metric": condition.get("metricKey", "Unknown"),
                            "Status": condition.get("status", "Unknown"),
                            "Actual": condition.get("actualValue"),
                            "Comparator": condition.get("comparator"),
                            "Threshold": condition.get("errorThreshold"),
                        }
                        for condition in snapshot.conditions
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("No quality gate conditions were returned by the server.")
        client = SonarClient(server_url, token)
        try:
            st.link_button("Open project in SonarQube", client.project_url(snapshot.key, branch))
        finally:
            client.close()

st.divider()
st.caption("Dashboard designed by Ramesh N")
