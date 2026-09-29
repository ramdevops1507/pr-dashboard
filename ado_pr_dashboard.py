"""
Azure DevOps Pull Request Dashboard
------------------------------------
Scans ALL repositories in a project for pull requests raised in a given
date range and shows: user, PR title, status, approver(s), and (optionally)
lines added/removed per PR.

Setup:
    pip install streamlit requests pandas

Run:
    streamlit run ado_pr_dashboard.py

Auth:
    You need an Azure DevOps Personal Access Token (PAT) with at least
    "Code (Read)" scope: https://dev.azure.com/{your_org}/_usersSettings/tokens
"""

import base64
import difflib
import re
import requests
import pandas as pd
import altair as alt
import streamlit as st
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta

# =====================================================================
#  CONFIGURATION — org/project are fine to keep here since they're not
#  secret. The PAT and password live in .streamlit/secrets.toml instead —
#  a separate file that's never inside this .py file, so sharing/backing up
#  this script can't accidentally leak either of them. See secrets.toml.example.
# =====================================================================
ADO_ORG = "BootsCCoE"
ADO_PROJECT = "RPI-BUK-Mobile"

# Only these repos are scanned — add/remove names here as needed.
ALLOWED_REPOS = {
    "dev-maos-boots",
    "dev-maos-framework",
    "dev-maos-login",
    "dev-maos-offers",
    "dev-maos-pillreminder",
    "dev-maos-portable",
    "dev-maos-shop",
    "dev-maos-storelocator",
    "dev-mios-boots",
    "dev-mios-buildscripts",
    "dev-mios-framework",
    "dev-mios-offers",
    "dev-mios-pillreminder",
    "dev-mios-podspecs",
    "dev-mios-portable",
    "dev-mios-storelocator",
    "dev-mios-sprinklr",
}
# =====================================================================

API_VERSION = "7.1"
MAX_FILES_PER_PR_FOR_DIFF = 200  # safety cap so one huge PR doesn't stall the fetch — raise/lower as needed
MAX_WORKERS = 10  # parallel threads for per-PR detail fetching (test detection / line diffs)


def get_platform(repo_name: str) -> str:
    """Derive platform from repo naming convention: *maos* -> Android, *mios* -> iOS."""
    n = repo_name.lower()
    if "maos" in n:
        return "Android"
    if "mios" in n:
        return "iOS"
    return "Other"

# Filename patterns that mark a file as a test file. Tune this for your
# codebases — currently tuned for iOS (Swift/XCTest) and Android (Kotlin/Java) repos.
TEST_PATH_MARKERS = ("/test/", "/tests/", "/__tests__/", "/androidtest/", "/spec/", "/specs/", "/uitests/")
TEST_FILENAME_SUFFIXES = (
    "test.swift", "tests.swift", "uitests.swift",
    "test.kt", "tests.kt", "test.java", "tests.java",
    "test.cs", "tests.cs",
    ".test.js", ".test.ts", ".test.jsx", ".test.tsx",
    ".spec.js", ".spec.ts", ".spec.jsx", ".spec.tsx",
    "_test.py", "test.py",
)


def is_test_file(path: str) -> bool:
    if not path:
        return False
    p = path.lower()
    filename = p.rsplit("/", 1)[-1]
    if any(marker in p for marker in TEST_PATH_MARKERS):
        return True
    if any(filename.endswith(suffix) for suffix in TEST_FILENAME_SUFFIXES):
        return True
    if filename.startswith("test_"):
        return True
    return False

# ----------------------------- Page setup -----------------------------

st.set_page_config(page_title="PR Analytics", page_icon="🔀", layout="wide")
st.markdown("""
<style>
.stApp {background: linear-gradient(135deg,#EAF1FF 0%,#F7F9FF 45%,#E6EEFC 100%);}
[data-testid="stSidebar"] {background: linear-gradient(180deg,#F3F6FF 0%,#DFE9FF 100%);}
.block-container {padding-top: 1.6rem;}
.hero {background: linear-gradient(120deg,#0B2E6F 0%,#1F5FD6 60%,#3B8CFF 100%);
       padding: 26px 32px; border-radius: 16px; color: #fff; margin-bottom: 18px;
       box-shadow: 0 8px 24px rgba(15,98,254,.25);}
.hero-title {font-size: 30px; font-weight: 800;}
.hero-sub {font-size: 14px; opacity: .92; margin-top: 6px;}
div[data-testid="stMetric"] {background:#fff; border-radius:14px; padding:16px 18px;
       box-shadow:0 2px 10px rgba(20,40,90,.08); border-left:5px solid #2F6FED;}
div[data-testid="stMetricValue"] {font-weight:800; color:#0B2E6F;}
div[class*="st-key-card_"] {background:#fff; border-radius:14px; padding:16px 18px;
       box-shadow:0 2px 10px rgba(20,40,90,.07); margin-bottom: 8px;}
</style>
""", unsafe_allow_html=True)

if "ADO_PAT" not in st.secrets:
    st.error(
        "Missing .streamlit/secrets.toml (or it's missing ADO_PAT). "
        "Create that file — see secrets.toml.example for the format — and restart the app."
    )
    st.stop()

ADO_PAT = st.secrets["ADO_PAT"]
DASHBOARD_PASSWORD = st.secrets.get("DASHBOARD_PASSWORD")  # optional — unset means no login screen

# ----------------------------- Password gate -----------------------------
# Only enforced if DASHBOARD_PASSWORD is set in secrets.toml. Leave it unset
# for localhost-only use; set it before exposing this on a network/public URL.
if DASHBOARD_PASSWORD:
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if not st.session_state["authenticated"]:
        st.markdown("### 🔒 PR Analytics — Sign in")
        with st.form("login_form"):
            entered = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Enter")
        if submitted:
            if entered == DASHBOARD_PASSWORD:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        st.stop()


# ----------------------------- Sidebar / filters ------------------------

with st.sidebar:
    st.header("Date range")
    default_start = date.today() - timedelta(days=30)
    start_date = st.date_input("From", value=default_start)
    end_date = st.date_input("To", value=date.today())

    st.header("Filters")
    platform_filter = st.multiselect("Platform", ["Android", "iOS"], default=["Android", "iOS"])
    status_filter = st.selectbox("PR Status", ["All", "Active", "Completed", "Abandoned"], index=0)
    user_filter = st.text_input("Filter by user (name or email contains)")
    previous_pr_df = st.session_state.get("pr_df", pd.DataFrame())
    available_users = (
        sorted(previous_pr_df["User"].dropna().astype(str).unique())
        if not previous_pr_df.empty
        else []
    )
    excluded_users = st.multiselect(
        "Exclude users",
        options=available_users,
        key="excluded_users",
        help="Selected users are omitted from the KPIs, charts, test coverage, and PR details.",
    )
    include_line_stats = st.checkbox(
        "Include lines added/removed (slower — fetches per-file diffs)", value=False
    )
    st.caption("Test-file detection (TestFiles / TestAdded? / Test%) uses changed file paths and names; it does not measure code coverage.")
    st.caption("The user filter applies instantly to already-fetched data — no need to re-click Fetch.")

    fetch = st.button("🔄 Fetch Pull Requests", type="primary")

st.markdown(
    f"""<div class="hero"><div class="hero-title">🔀 Pull Request Analytics</div>
    <div class="hero-sub">{ADO_PROJECT} &nbsp;·&nbsp; {start_date:%d %b %Y} – {end_date:%d %b %Y}
    &nbsp;·&nbsp; {' + '.join(platform_filter) if platform_filter else 'No platform selected'}</div></div>""",
    unsafe_allow_html=True,
)


def get_auth_header() -> dict:
    token_b64 = base64.b64encode(f":{ADO_PAT}".encode()).decode()
    return {"Authorization": f"Basic {token_b64}"}


def ado_get(url: str, params: dict = None) -> dict:
    resp = requests.get(url, headers=get_auth_header(), params=params or {}, timeout=30)
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=300, show_spinner=False)
def list_repositories(org: str, proj: str) -> list:
    """All repos in the project."""
    url = f"https://dev.azure.com/{org}/{proj}/_apis/git/repositories"
    data = ado_get(url, {"api-version": API_VERSION})
    return [{"id": r["id"], "name": r["name"]} for r in data.get("value", [])]


def fetch_prs_for_repo(org: str, proj: str, repo_id: str, status: str, min_time: str, max_time: str) -> list:
    """PRs for one repo, filtered server-side by status and date range."""
    url = f"https://dev.azure.com/{org}/{proj}/_apis/git/repositories/{repo_id}/pullrequests"
    params = {
        "api-version": API_VERSION,
        "searchCriteria.status": status,
        "searchCriteria.minTime": min_time,
        "searchCriteria.maxTime": max_time,
    }
    all_prs, skip, top = [], 0, 100
    while True:
        params.update({"$top": top, "$skip": skip})
        data = ado_get(url, params)
        batch = data.get("value", [])
        all_prs.extend(batch)
        if len(batch) < top:
            break
        skip += top
    return all_prs


def get_latest_iteration_id(org: str, proj: str, repo_id: str, pr_id: int) -> int:
    url = f"https://dev.azure.com/{org}/{proj}/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}/iterations"
    data = ado_get(url, {"api-version": API_VERSION})
    iterations = data.get("value", [])
    return iterations[-1]["id"] if iterations else None


def get_iteration_changes(org: str, proj: str, repo_id: str, pr_id: int, iteration_id: int) -> list:
    url = (
        f"https://dev.azure.com/{org}/{proj}/_apis/git/repositories/{repo_id}"
        f"/pullRequests/{pr_id}/iterations/{iteration_id}/changes"
    )
    data = ado_get(url, {"api-version": API_VERSION})
    return data.get("changeEntries", [])


@st.cache_data(ttl=3600, show_spinner=False)
def get_blob_text(org: str, proj: str, repo_id: str, object_id: str) -> str:
    if not object_id:
        return ""
    url = f"https://dev.azure.com/{org}/{proj}/_apis/git/repositories/{repo_id}/blobs/{object_id}"
    resp = requests.get(
        url, headers=get_auth_header(), params={"api-version": API_VERSION, "$format": "text"}, timeout=30
    )
    if resp.status_code != 200:
        return ""
    return resp.text


@st.cache_data(ttl=300, show_spinner=False)
def get_pr_file_changes(org: str, proj: str, repo_id: str, pr_id: int) -> list:
    """List of changed files (not folders) in a PR's latest iteration."""
    try:
        iteration_id = get_latest_iteration_id(org, proj, repo_id, pr_id)
        if iteration_id is None:
            return []
        changes = get_iteration_changes(org, proj, repo_id, pr_id, iteration_id)
        return [c for c in changes if not c.get("item", {}).get("isFolder")]
    except Exception:
        return []


# Files whose diffs are noise, not authored code: lockfiles get fully rewritten
# by tooling, generated Xcode/build files churn on every build, minified/binary
# files aren't human-edited line-by-line. Counting these inflates "lines
# added/removed" with numbers that have nothing to do with what a person wrote.
LINE_DIFF_EXCLUDE_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "podfile.lock",
    "gemfile.lock", "composer.lock", "cargo.lock", "poetry.lock",
}
LINE_DIFF_EXCLUDE_SUFFIXES = (
    ".lock", ".min.js", ".min.css", ".map",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".svg",
    ".zip", ".jar", ".so", ".dylib", ".a", ".framework", ".ipa", ".apk", ".aab",
    ".pbxproj", ".xcworkspacedata", ".xcuserstate", ".xcsettings", ".nib", ".storyboardc",
)
# Safety net: if a single file's diff still comes out enormous (e.g. a
# generated file we didn't think to exclude by name), treat it as noise
# rather than let it dominate a person's totals.
MAX_LINES_PER_FILE_DIFF = 5000


def should_skip_line_diff(path: str) -> bool:
    if not path:
        return True
    p = path.lower()
    filename = p.rsplit("/", 1)[-1]
    if filename in LINE_DIFF_EXCLUDE_NAMES:
        return True
    if any(filename.endswith(suf) for suf in LINE_DIFF_EXCLUDE_SUFFIXES):
        return True
    return False


def compute_line_diff(org: str, proj: str, repo_id: str, file_changes: list) -> tuple:
    """Best-effort lines-added / lines-removed, given an already-fetched change list.
    Excludes lockfiles/generated/binary files, and caps any single file's
    contribution so one anomalous file can't blow up a person's totals."""
    try:
        countable = [c for c in file_changes if not should_skip_line_diff(c.get("item", {}).get("path"))]
        if len(countable) > MAX_FILES_PER_PR_FOR_DIFF:
            return ("N/A (too many files)", "N/A (too many files)")

        added_total, removed_total = 0, 0
        for change in countable:
            item = change.get("item", {})
            new_obj = item.get("objectId")
            old_obj = item.get("originalObjectId")

            new_text = get_blob_text(org, proj, repo_id, new_obj) if new_obj else ""
            old_text = get_blob_text(org, proj, repo_id, old_obj) if old_obj else ""

            file_added, file_removed = 0, 0
            diff = difflib.unified_diff(old_text.splitlines(), new_text.splitlines(), lineterm="")
            for line in diff:
                if line.startswith("+++") or line.startswith("---"):
                    continue
                if line.startswith("+"):
                    file_added += 1
                elif line.startswith("-"):
                    file_removed += 1

            if file_added + file_removed > MAX_LINES_PER_FILE_DIFF:
                continue  # anomalous single file — likely generated content we didn't catch by name

            added_total += file_added
            removed_total += file_removed

        return (added_total, removed_total)
    except Exception:
        return ("error", "error")


def flatten_pr(pr: dict, repo_name: str, include_diff: bool) -> dict:
    created = pr.get("creationDate", "")
    try:
        created_fmt = datetime.fromisoformat(created.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        created_fmt = created

    reviewers = pr.get("reviewers", [])
    approved_by = ", ".join(r.get("displayName", "") for r in reviewers if r.get("vote", 0) >= 5)

    created_by = pr.get("createdBy", {})
    repo_id = pr.get("repository", {}).get("id")
    pr_id = pr.get("pullRequestId")

    web_url = f"https://dev.azure.com/{ADO_ORG}/{ADO_PROJECT}/_git/{repo_name}/pullrequest/{pr_id}"

    # File list is needed for test-file detection regardless of the line-diff
    # option, and is reused for the diff calc below so it's only fetched once.
    file_changes = get_pr_file_changes(ADO_ORG, ADO_PROJECT, repo_id, pr_id)
    file_paths = [c.get("item", {}).get("path") or "" for c in file_changes]
    total_files = len(file_paths)
    test_file_count = sum(1 for p in file_paths if is_test_file(p))
    test_added = "Yes" if test_file_count > 0 else "No"
    test_percent = round(test_file_count / total_files * 100, 1) if total_files else 0.0

    lines_added, lines_removed = (None, None)
    if include_diff:
        lines_added, lines_removed = compute_line_diff(ADO_ORG, ADO_PROJECT, repo_id, file_changes)

    return {
        "User": created_by.get("displayName"),
        "User Email": created_by.get("uniqueName", ""),
        "PR_ID": pr_id,
        "Title": pr.get("title"),
        "Status": pr.get("status"),
        "Approved By": approved_by,
        "Repository": repo_name,
        "Platform": get_platform(repo_name),
        "Created": created_fmt,
        "TestFiles": test_file_count,
        "TestAdded?": test_added,
        "Test%": test_percent,
        "Lines Added": lines_added,
        "Lines Removed": lines_removed,
        "Link": web_url,
    }


# ----------------------------- Main logic ------------------------------

if fetch:
    if start_date > end_date:
        st.error("'From' date must be before 'To' date.")
        st.stop()

    min_time = f"{start_date.isoformat()}T00:00:00Z"
    max_time = f"{end_date.isoformat()}T23:59:59Z"

    with st.spinner("Fetching repositories..."):
        try:
            all_repos = list_repositories(ADO_ORG, ADO_PROJECT)
        except requests.exceptions.HTTPError as e:
            st.error(f"ADO API error: {e.response.status_code} — check ADO_ORG / ADO_PROJECT at the top of the script and ADO_PAT in secrets.toml.")
            st.stop()
        except Exception as e:
            st.error(f"Failed to list repositories: {e}")
            st.stop()

    repos = [r for r in all_repos if r["name"] in ALLOWED_REPOS]
    missing = ALLOWED_REPOS - {r["name"] for r in repos}
    if missing:
        st.warning(f"These repos in ALLOWED_REPOS weren't found in the project (check spelling): {', '.join(sorted(missing))}")

    repos = [r for r in repos if get_platform(r["name"]) in platform_filter]
    if not repos:
        st.warning("No repos match the selected Platform filter.")
        st.stop()

    # Step 1: list PRs per repo (fast — a handful of calls per repo).
    pr_repo_pairs = []
    scan_report = []  # transparency: what happened for each repo, so results are verifiable
    progress = st.progress(0.0, text="Listing pull requests across repositories...")
    for i, repo in enumerate(repos):
        progress.progress((i + 1) / max(len(repos), 1), text=f"Listing PRs in {repo['name']}...")
        try:
            raw_prs = fetch_prs_for_repo(ADO_ORG, ADO_PROJECT, repo["id"], status_filter, min_time, max_time)
        except Exception as e:
            scan_report.append({"Repository": repo["name"], "Platform": get_platform(repo["name"]), "PRs Found": "ERROR", "Detail": str(e)})
            continue  # skip repos the PAT can't access, rather than failing the whole run
        scan_report.append({"Repository": repo["name"], "Platform": get_platform(repo["name"]), "PRs Found": len(raw_prs), "Detail": ""})
        for pr in raw_prs:
            pr_repo_pairs.append((pr, repo["name"]))
    progress.empty()

    st.session_state["scan_report"] = pd.DataFrame(scan_report)
    if not st.session_state["scan_report"].empty:
        st.session_state["scan_report"]["PRs Found"] = st.session_state["scan_report"]["PRs Found"].apply(str)

    # Step 2: per-PR detail work (test-file detection, optional line diffs) —
    # this is the slow part (2+ HTTP calls per PR), so run it in parallel.
    all_rows = []
    total = len(pr_repo_pairs)
    if total:
        progress = st.progress(0.0, text=f"Fetching details for {total} PRs...")
        done = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [
                executor.submit(flatten_pr, pr, repo_name, include_line_stats)
                for pr, repo_name in pr_repo_pairs
            ]
            for future in as_completed(futures):
                try:
                    all_rows.append(future.result())
                except Exception:
                    pass  # skip any single PR that errors out, rather than failing the whole run
                done += 1
                progress.progress(done / total, text=f"Fetching details for {total} PRs... ({done}/{total})")
        progress.empty()

    df = pd.DataFrame(all_rows)
    if not df.empty:
        # Lines Added/Removed can mix ints, None, and strings like "N/A (too many
        # files)" or "error" — force to string so Arrow (used by st.dataframe)
        # doesn't choke trying to infer a single numeric dtype for the column.
        for col in ("Lines Added", "Lines Removed"):
            if col in df.columns:
                df[col] = df[col].apply(lambda v: "" if v is None else str(v))
    st.session_state["pr_df"] = df
    st.rerun()

# --- chart helpers start ---
STATUS_COLORS = {"Completed": "#2E9E6B", "Active": "#2F6FED", "Abandoned": "#E5484D"}
PLATFORM_COLORS = {"Android": "#34A853", "iOS": "#5B6CFF"}
TEST_COLORS = {"With tests": "#2E9E6B", "Without tests": "#F5A524"}


def donut(data, cat, val, colors, height=250):
    present = [k for k in colors if k in set(data[cat])]
    return (
        alt.Chart(data)
        .mark_arc(innerRadius=62, outerRadius=105, stroke="#ffffff", strokeWidth=2)
        .encode(
            theta=alt.Theta(f"{val}:Q"),
            color=alt.Color(
                f"{cat}:N",
                scale=alt.Scale(domain=present, range=[colors[k] for k in present]),
                legend=alt.Legend(orient="bottom", title=None),
            ),
            tooltip=[alt.Tooltip(f"{cat}:N"), alt.Tooltip(f"{val}:Q", title="PRs")],
        )
        .properties(height=height)
    )


def ranked_bar(data, label, value, color):
    """Horizontal bars with value labels, largest first."""
    base = alt.Chart(data).encode(
        y=alt.Y(f"{label}:N", sort="-x", title=None, axis=alt.Axis(labelLimit=220)),
        x=alt.X(f"{value}:Q", title=None),
    )
    bars = base.mark_bar(cornerRadiusEnd=4, size=16).encode(color=color, tooltip=[label, value])
    labels = base.mark_text(align="left", dx=4, color="#334155").encode(text=f"{value}:Q")
    return (bars + labels).properties(height=max(200, 26 * len(data) + 20))


def trend_chart(d):
    d = d.dropna(subset=["Created_dt"]).sort_values("Created_dt")
    span = (d["Created_dt"].max() - d["Created_dt"].min()).days
    rule = "W" if span > 45 else "D"
    ts = d.set_index("Created_dt").resample(rule)["PR_ID"].count().reset_index(name="PRs")
    base = alt.Chart(ts).encode(
        x=alt.X("Created_dt:T", title=None),
        y=alt.Y("PRs:Q", title=None),
        tooltip=[alt.Tooltip("Created_dt:T", title="Date"), alt.Tooltip("PRs:Q")],
    )
    area = base.mark_area(
        opacity=0.25, color="#2F6FED", line={"color": "#2F6FED", "strokeWidth": 2.5}, interpolate="monotone"
    )
    points = base.mark_point(color="#2F6FED", filled=True, size=45)
    return (area + points).properties(height=260), ("weekly" if rule == "W" else "daily")
# --- chart helpers end ---


def card(key: str):
    return st.container(key=f"card_{key}")


if "pr_df" in st.session_state:
    df = st.session_state["pr_df"]

    tab_overview, tab_tests, tab_details = st.tabs(["📊 Overview", "🧪 Test-file signals", "📋 PR Details"])

    if not df.empty and user_filter:
        name_match = df["User"].str.contains(user_filter, case=False, na=False)
        email_match = df["User Email"].str.contains(user_filter, case=False, na=False)
        df = df[name_match | email_match]

    if not df.empty and excluded_users:
        df = df[~df["User"].isin(excluded_users)]

    if df.empty:
        st.info("No pull requests found for this date range / status / user filter across any repo in the project.")
    else:
        df = df.copy()
        df["Created_dt"] = pd.to_datetime(df["Created"], errors="coerce")
        df["Status_clean"] = df["Status"].astype(str).str.capitalize()

        # ----------------------- Overview tab -----------------------
        with tab_overview:
            # ----------------------- KPI cards -----------------------
            k1, k2, k3, k4, k5 = st.columns(5)
            k1.metric("Total PRs", len(df))
            k2.metric("Contributors", df["User"].nunique())
            k3.metric("Repositories", df["Repository"].nunique())
            k4.metric("PRs with detected test files", f"{round((df['TestAdded?'] == 'Yes').mean() * 100, 1)}%")
            k5.metric("Completed", f"{round((df['Status_clean'] == 'Completed').mean() * 100, 1)}%")

            st.write("")
            c1, c2 = st.columns(2)
            with c1:
                with card("status"):
                    st.markdown("**PR status breakdown**")
                    status_df = df["Status_clean"].value_counts().rename_axis("Status").reset_index(name="PRs")
                    st.altair_chart(donut(status_df, "Status", "PRs", STATUS_COLORS))
            with c2:
                with card("platform"):
                    st.markdown("**Android vs iOS**")
                    plat_df = df["Platform"].value_counts().rename_axis("Platform").reset_index(name="PRs")
                    st.altair_chart(donut(plat_df, "Platform", "PRs", PLATFORM_COLORS))

            with card("trend"):
                trend, grain = trend_chart(df)
                st.markdown(f"**PRs raised over time** ({grain})")
                st.altair_chart(trend)

            c3, c4 = st.columns(2)
            with c3:
                with card("top_users"):
                    top_n = st.slider("Top contributors to show", 5, 25, 10, key="top_n")
                    st.markdown(f"**Top {top_n} contributors by PRs raised**")
                    users_df = df["User"].value_counts().head(top_n).rename_axis("User").reset_index(name="PRs")
                    st.altair_chart(ranked_bar(users_df, "User", "PRs", alt.value("#2F6FED")))
            with c4:
                with card("repos"):
                    st.markdown("**PR activity by repository**")
                    repo_df = df.groupby(["Repository", "Platform"]).size().reset_index(name="PRs")
                    color = alt.Color(
                        "Platform:N",
                        scale=alt.Scale(domain=list(PLATFORM_COLORS), range=list(PLATFORM_COLORS.values())),
                        legend=alt.Legend(orient="bottom", title=None),
                    )
                    st.altair_chart(ranked_bar(repo_df, "Repository", "PRs", color))

        # ----------------------- Test-file signals tab ------------------
        with tab_tests:
            test_summary = (
                df.groupby("User")
                .agg(
                    Total_PRs=("PR_ID", "count"),
                    PRs_With_Tests=("TestAdded?", lambda s: (s == "Yes").sum()),
                )
                .reset_index()
            )
            test_summary["Test Coverage %"] = (
                test_summary["PRs_With_Tests"] / test_summary["Total_PRs"] * 100
            ).round(1)

            t1, t2 = st.columns([1, 2])
            with t1:
                with card("test_donut"):
                    st.markdown("**PRs with vs without detected test files**")
                    n_yes = int((df["TestAdded?"] == "Yes").sum())
                    tests_df = pd.DataFrame({"Result": ["With tests", "Without tests"], "PRs": [n_yes, len(df) - n_yes]})
                    st.altair_chart(donut(tests_df, "Result", "PRs", TEST_COLORS))
                    for plat in ("Android", "iOS"):
                        sub = df[df["Platform"] == plat]
                        if len(sub):
                            st.metric(f"{plat} PRs with detected test files", f"{round((sub['TestAdded?'] == 'Yes').mean() * 100, 1)}%")
            with t2:
                with card("test_users"):
                    min_prs = st.slider("Minimum PRs per user", 1, 10, 1, key="min_prs")
                    st.markdown("**Share of each contributor's PRs with detected test files**")
                    st.caption("This is the share of PRs with a changed file identified as a test file, not code coverage. Each bar is labeled with (n = total PRs); a 100% from 1 PR is a small sample.")
                    cov = test_summary[test_summary["Total_PRs"] >= min_prs].sort_values("Test Coverage %", ascending=False).head(20)
                    cov = cov.rename(columns={"Test Coverage %": "Coverage"})[["User", "Total_PRs", "Coverage"]]
                    if cov.empty:
                        st.info("No contributors meet the minimum PR count.")
                    else:
                        cov = cov.copy()
                        cov["User"] = cov["User"] + " (n=" + cov["Total_PRs"].astype(str) + ")"
                        cov_color = alt.Color(
                            "Coverage:Q",
                            scale=alt.Scale(domain=[0, 50, 100], range=["#E5484D", "#F5A524", "#2E9E6B"]),
                            legend=None,
                        )
                        st.altair_chart(ranked_bar(cov[["User", "Coverage"]], "User", "Coverage", cov_color))

            with st.expander("Test-file signals by contributor"):
                st.dataframe(
                    test_summary.sort_values("Test Coverage %", ascending=True),
                    width='stretch',
                    hide_index=True,
                    column_config={
                        "Test Coverage %": st.column_config.ProgressColumn(
                            "Test Coverage %", min_value=0, max_value=100, format="%.1f%%"
                        ),
                    },
                )

        # ----------------------- PR Details tab -----------------------
        with tab_details:
            status_labels = {"completed": "✅ Completed", "active": "🔄 Active", "abandoned": "❌ Abandoned"}
            table_df = df.drop(columns=["User Email", "Created_dt", "Status_clean"], errors="ignore")
            table_df["Status"] = table_df["Status"].map(lambda s: status_labels.get(str(s).lower(), s))
            st.dataframe(
                table_df,
                width='stretch',
                hide_index=True,
                column_config={
                    "Link": st.column_config.LinkColumn("Open PR", display_text="View →"),
                    "Title": st.column_config.TextColumn("Title", width="large"),
                    "Approved By": st.column_config.TextColumn("Approved By", width="medium"),
                    "Test%": st.column_config.ProgressColumn(
                        "Test-file share (%)",
                        help=(
                            "Matched changed test files divided by all changed files. This is not code coverage: "
                            "it does not show how much changed code is tested. False positive: one code file plus "
                            "two test files can show 67%, even if those tests do not cover that code. False negative: "
                            "10 code files plus one test file shows about 9%, even if that one test file covers all "
                            "10 files. If code and tests are in separate PRs, the code PR can show 0% while the "
                            "test-only PR shows 100%. Treat this only as a rough changed-file mix indicator."
                        ),
                        min_value=0,
                        max_value=100,
                        format="%.0f%%",
                    ),
                },
            )
            csv = table_df.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Download as CSV", csv, "ado_pull_requests.csv", "text/csv")

    if "scan_report" in st.session_state:
        with st.expander("🔍 Data verification — repositories scanned this run"):
            st.dataframe(st.session_state["scan_report"], width='stretch', hide_index=True)
            st.caption("0 = no PRs in the selected range; ERROR = the PAT can't access that repo.")
else:
    st.info("Pick a date range in the sidebar and click **Fetch Pull Requests**.")
