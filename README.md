# Azure DevOps PR Analytics

A Streamlit dashboard for reviewing pull-request activity across the configured Azure DevOps repositories, including PR status, contributors, test-file signals, and optional line-change estimates.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run ado_pr_dashboard.py
```

Create `.streamlit/secrets.toml` locally with an Azure DevOps PAT that has Code (Read) access:

```toml
ADO_PAT = "your-azure-devops-personal-access-token"
DASHBOARD_PASSWORD = "choose-a-dashboard-password"
```

`DASHBOARD_PASSWORD` is optional for local-only use. Set it when the app is available to other users. Never commit `secrets.toml` or put credentials in source code.

## Deploy with Streamlit Community Cloud

1. Sign in to Streamlit Community Cloud with a GitHub account that can access this private repository.
2. Create an app from `ramdevops1507/pr-dashboard`, branch `main`, with `ado_pr_dashboard.py` as the main file.
3. In the app's settings, add the required secrets using the TOML format above. Keep the values in Streamlit's Secrets settings, not in GitHub.
4. Deploy and check the app logs if the Azure DevOps API cannot be reached or the PAT lacks repository access.

The deployment environment must be able to reach `dev.azure.com`. If your organization restricts Azure DevOps to a corporate network or VPN, Streamlit Community Cloud may not be able to connect; deploy in an approved environment with the required network access instead.