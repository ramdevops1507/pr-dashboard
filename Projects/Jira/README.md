# Jira Dashboard

Sprint-wise Streamlit dashboard for Jira Cloud, running on **8503**.

## Setup

Run from the repository root in your virtual environment:

```bash
pip install -r Projects/Jira/requirements.txt
streamlit run Projects/Jira/streamlit_app.py --server.port 8503
```

Open http://localhost:8503. ADO stays on 8502 and Sonar on 8501.

Add these top-level entries to the existing `.streamlit/secrets.toml`, preserving
your ADO and Sonar credentials:

```toml
JIRA_URL = "https://your-company.atlassian.net"
JIRA_EMAIL = "your-account-email"
JIRA_API_TOKEN = "your-jira-cloud-api-token"
# Optional: your Jira instance's Flagged field ID (do not guess it).
# JIRA_FLAGGED_FIELD = "customfield_10021"
```

Use the real URL instead of the masked `xxxxxxx` hostname. Authentication uses
HTTP Basic with email + API token, encoded by Requests automatically. Do not use
the `Bearer` PAT pattern for this app. The account needs access to the requested
sprints/boards and Browse Projects permission; issue security can limit visible
results. Use an API token compatible with direct site-URL Basic authentication.
Scoped tokens requiring Atlassian's gateway URL are not supported by this version.
Never share or commit tokens.

## Sprint reporting

The sidebar defaults to `6425, 6419, 6413, 6166, 6243, 6681, 6643, 6473`.
Replace them each sprint, then select **Fetch / refresh report**. Duplicate IDs
are removed while keeping their order. IDs are entered manually; a board ID is
not required. Dates, state, and goal come from Jira, not a hard-coded 10-day duration.

- Sprint comparison: visible issue counts, completion percentage, unassigned issues.
- Current status categories: To do, In progress, Done, and explicit Unknown.
- Assignee/status breakdowns, issue types, priorities, and Jira issue links.
- Presentation layout with a Jira-only gradient background, header, and KPI cards.
- Interactive Altair charts: sprint delivery, Bugs vs Defects, status mix, issue
  types, quality issues by priority/status, and assignee workload.
- Bug/Defect KPI cards use exact issue type names (case-insensitive); open quality
  issues are those outside the Done category. Unknown categories are included as open.
- Main report includes only Story, Bug, and Defect (exact names, case-insensitive),
  excluding Jira's subtask issue types even if they have one of those names.
  This scope applies to all comparison counts, cards, charts, tables, and main CSV exports.
- Subtask details are hidden by default. Select **Show subtask details** to view
  and export them separately without changing the main report.
- **Issues by assignee** includes issue-count labels. Counts are not effort
  estimates or individual performance scores.
- Charts describe in-scope sprint snapshots; issue-table filters do not change cards
  or charts. Submitted sprint order is preserved, not presented as a time trend.
- Filterable issue table and CSV exports.
- Per-sprint errors and clearly marked partial results.
- All issue pages are fetched; failed/inconsistent pagination is not reported as success.
- Fetch refreshes each selected sprint; filters reuse the fetched snapshot.

**This is a current snapshot, not a historical Jira Sprint Report.** A closed
sprint's issues may have changed since closure. Done means Jira's `done` status
category, not a guessed status name. An issue in multiple sprints is counted
within each sprint separately; no cross-sprint total is presented. Zero-issue
sprints show `N/A` completion rather than 0% or 100%.

Flagged information requires the optional field ID. Flagged is not automatically
equivalent to a custom Blocked status. Story points, committed scope, sprint-close
completion, spillover, velocity, and burndown are not implemented in this version.

## Deployment and troubleshooting

For Streamlit Community Cloud, create a separate app with:

- Repository: `ramdevops1507/pr-dashboard`
- Branch: `main`
- Main file path: `Projects/Jira/streamlit_app.py`
- Python version: 3.11 or newer

Add only the required Jira settings in **Advanced settings > Secrets** (or the
app's **Settings > Secrets** after creation). Do not upload the local secrets file,
which may also contain ADO and Sonar credentials. Keep the app private and grant
access only to approved viewers before configuring credentials or fetching data.
If private hosting is unavailable for your account, use an approved authenticated
hosting environment instead.

Port **8503** is for local development only. Community Cloud manages its own port
and provides an HTTPS `.streamlit.app` URL; do not configure a Cloud port of 8503.
Verify startup and fetch at least one accessible sprint before sharing the URL.

Deploy `Projects/Jira/streamlit_app.py` from `main`, with credentials in deployment
secrets. Its adjacent `requirements.txt` supplies the app's dependencies. Protect
shared deployments with platform-level authentication/SSO: the Jira API token
authenticates API calls, not dashboard visitors, and the ADO password gate is not used.

401/403: check email/token and permissions. 404: sprint ID may not exist or may
be inaccessible. 429: wait before refreshing. DNS/TLS/proxy errors require approved
network configuration; HTTPS verification is never disabled.

## Tests

```bash
python -m unittest discover -s Projects/Jira/tests -v
```

Tests use mocked APIs; no credentials or Jira network access are required.
