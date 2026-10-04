# Sonar Dashboard

Streamlit dashboard for `BootsApp-iOS` and `BootsApp-android` on
`https://sonarqube.cep.boots.com`.

## Setup

From the repository root, using your Python virtual environment:

```bash
pip install -r Projects/Sonar/requirements.txt
streamlit run Projects/Sonar/streamlit_app.py --server.port 8501
```

Add these entries to the existing root `.streamlit/secrets.toml` without removing
the Azure DevOps entries:

```toml
SONAR_TOKEN = "your-sonarqube-token"
SONAR_URL = "https://sonarqube.cep.boots.com"
SONAR_PROJECTS = ["BootsApp-iOS", "BootsApp-android"]
```

Use actual **project keys**, which may differ from display names. Find a project's
key in its SonarQube dashboard URL (`id=...`). The token requires Browse permission
on both projects and must be allowed to access the Web API. It is sent via HTTP
Basic authentication (token as username, empty password) over verified HTTPS.
Never commit secrets or paste the token into chat.

## Features

- Project comparison and CSV export.
- Quality gate status and server-reported conditions, including new-code conditions.
- Overall-code bugs, vulnerabilities, code smells, security hotspots, coverage,
  duplication, lines of code, and technical debt in minutes.
- Main-branch analysis by default; an optional named branch for servers supporting it.
- Last analysis timestamp and fetch timestamp.
- Five-minute bounded cache, with a manual refresh for selected projects.
- Explicit per-project errors; successful projects remain visible if another fails.

Metric availability is discovered from the server catalog. Unsupported or absent
measures show `N/A`, never zero. This dashboard uses the traditional SonarQube
metric keys; a server configured only for newer software-quality metrics may
leave some cards unavailable. It does not trigger scans, change quality gates,
or display individual issues. Gate status comes from SonarQube, not locally
invented thresholds.

## Hosting and troubleshooting

Sonar uses port **8501** (http://localhost:8501). Azure DevOps uses port
**8502** (http://localhost:8502), so both apps can run at the same time:

```bash
streamlit run Projects/Sonar/streamlit_app.py --server.port 8501
```

The runtime needs corporate network/VPN access to the SonarQube server. If TLS
uses a corporate CA, set `REQUESTS_CA_BUNDLE` to your approved CA bundle;
certificate verification is never disabled.

For deployment, select `Projects/Sonar/streamlit_app.py` on `main` and configure
secrets in the hosting platform. The adjacent `requirements.txt` contains this
app's dependencies. Deploy only in an approved environment with server access
and platform-level authentication or SSO. The Sonar API token authenticates the
dashboard to SonarQube, **not visitors to the dashboard**; this app has no built-in
visitor login and does not use the ADO app's `DASHBOARD_PASSWORD`.

401/403 means the token or permissions need attention. For 404, check the project
key, branch, and server URL. Redirects or non-JSON responses may indicate an SSO
proxy intercepting API requests.

## Tests

From the repository root:

```bash
python -m unittest discover -s Projects/Sonar/tests -v
```

Tests use mocked API responses and do not need a token or network access.
