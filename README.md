# Dashboard Projects

A single repository on the `main` branch for independently maintained dashboards.

## Repository structure

```text
Projects/
├── ADO_PR_Dashboard/
│   ├── ado_pr_dashboard.py
│   └── requirements.txt
├── Jira/
│   ├── streamlit_app.py
│   ├── jira_client.py
│   ├── requirements.txt
│   ├── tests/
│   └── README.md
└── Sonar/
    ├── streamlit_app.py
    ├── sonar_client.py
    ├── requirements.txt
    ├── tests/
    └── README.md
.streamlit/
└── config.toml
requirements.txt
```

- [Azure DevOps PR Dashboard](Projects/ADO_PR_Dashboard/): implemented; reviews PR status, contributors, test-file signals, and optional line-change estimates.
- [Jira](Projects/Jira/): implemented; reports current issue counts and statuses by editable sprint IDs. See its README for authentication and snapshot limitations.
- [Sonar](Projects/Sonar/): implemented; compares mobile-app quality gates, coverage, duplication, and code-quality measures. See its README for secrets, network requirements, and launch instructions.

Keep each dashboard's entry point and dependencies in its own project folder.
The root `requirements.txt` currently delegates to the Azure DevOps project's requirements.
The root `ado_pr_dashboard.py` remains as a compatibility entry point for an existing
Streamlit Community Cloud app configured with its original main-file path.

## Local dashboard ports

| Dashboard | Port | URL |
|-----------|------|-----|
| Azure DevOps | 8502 | http://localhost:8502 |
| Sonar | 8501 | http://localhost:8501 |
| Jira | 8503 | http://localhost:8503 |

Use the explicit port in each launch command so all three dashboards can run together.

## Run the Azure DevOps dashboard locally

Run these commands from the repository root so Streamlit uses the existing root-level theme and local secrets:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run Projects/ADO_PR_Dashboard/ado_pr_dashboard.py --server.port 8502
```

Create `.streamlit/secrets.toml` locally with an Azure DevOps PAT that has Code (Read) access:

```toml
ADO_PAT = "your-azure-devops-personal-access-token"
DASHBOARD_PASSWORD = "choose-a-dashboard-password"
```

`DASHBOARD_PASSWORD` is optional for local-only use. Set it when the app is available to other users. Never commit `secrets.toml` or put credentials in source code.

## Deploy with Streamlit Community Cloud

1. Sign in to Streamlit Community Cloud with a GitHub account that can access this private repository.
2. Create an app from `ramdevops1507/pr-dashboard`, branch `main`, with `Projects/ADO_PR_Dashboard/ado_pr_dashboard.py` as the main file. For an existing deployment, update its main-file path or recreate the deployment with this path.
3. In the app's settings, add the required secrets using the TOML format above. Keep the values in Streamlit's Secrets settings, not in GitHub.
4. Deploy and check the app logs if the Azure DevOps API cannot be reached or the PAT lacks repository access.

The deployment environment must be able to reach `dev.azure.com`. If your organization restricts Azure DevOps to a corporate network or VPN, Streamlit Community Cloud may not be able to connect; deploy in an approved environment with the required network access instead.