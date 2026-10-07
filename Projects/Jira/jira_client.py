import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import requests


DEFAULT_SPRINT_IDS = "6425, 6419, 6413, 6166, 6243, 6681, 6643, 6473"
ISSUE_COLUMNS = [
    "Sprint ID", "Issue", "Summary", "Project", "Status", "Category",
    "Assignee", "Issue type", "Is subtask", "Priority", "Flagged", "Updated", "Jira link",
]
CATEGORY_LABELS = {"new": "To do", "indeterminate": "In progress", "done": "Done"}


class JiraError(Exception):
    """Configuration or API error safe to display without credentials."""


@dataclass(frozen=True)
class SprintReport:
    sprint_id: int
    name: str
    state: str
    goal: str
    start: str | None
    end: str | None
    completed: str | None
    issues: list[dict]
    fetched_at: str


def main_report(report: SprintReport) -> SprintReport:
    return replace(
        report,
        issues=[
            issue for issue in report.issues
            if not issue["Is subtask"]
            and issue["Issue type"].strip().casefold() in {"story", "bug", "defect"}
        ],
    )


def parse_sprint_ids(text: str) -> list[int]:
    text = text.strip()
    if not re.fullmatch(r"[0-9]+(?:(?:\s*,\s*|\s+)[0-9]+)*", text):
        raise JiraError("Enter positive sprint IDs separated by commas, spaces, or new lines.")
    parts = re.split(r"[,\s]+", text)
    ids = list(dict.fromkeys(int(part) for part in parts))
    if any(sprint_id <= 0 for sprint_id in ids):
        raise JiraError("Sprint IDs must be positive integers.")
    return ids


def validate_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        parsed.port
    except ValueError:
        raise JiraError("JIRA_URL contains an invalid hostname or port.") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise JiraError("JIRA_URL must be the HTTPS Jira Cloud base URL, without a path or credentials.")
    return url.rstrip("/")


def summarize(report: SprintReport) -> dict:
    counts = {category: 0 for category in ("To do", "In progress", "Done", "Unknown")}
    for issue in report.issues:
        counts[issue["Category"]] += 1
    total = len(report.issues)
    return {
        "Sprint ID": report.sprint_id,
        "Sprint": report.name,
        "Sprint state": report.state,
        "Start": report.start,
        "End": report.end,
        "Completed": report.completed,
        "Total issues": total,
        **counts,
        "Remaining": total - counts["Done"],
        "Done (%)": round(100 * counts["Done"] / total, 1) if total else None,
        "Unassigned": sum(issue["Assignee"] == "Unassigned" for issue in report.issues),
        "Flagged": (
            sum(issue["Flagged"] is True for issue in report.issues)
            if report.issues and all(issue["Flagged"] is not None for issue in report.issues)
            else None
        ),
        "Fetched at (UTC)": report.fetched_at,
    }


class JiraClient:
    def __init__(self, url: str, email: str, token: str):
        self.url = validate_url(url)
        if not email.strip() or not token.strip():
            raise JiraError("Set JIRA_EMAIL and JIRA_API_TOKEN in Streamlit secrets.")
        self.session = requests.Session()
        self.session.auth = (email.strip(), token.strip())
        self.session.headers["Accept"] = "application/json"

    def close(self) -> None:
        self.session.close()

    def get(self, endpoint: str, **params) -> dict:
        try:
            response = self.session.get(
                f"{self.url}/rest/agile/1.0/{endpoint}",
                params=params,
                timeout=(10, 45),
                allow_redirects=False,
            )
        except requests.exceptions.SSLError:
            raise JiraError("TLS validation failed. Use an approved CA bundle; do not disable HTTPS verification.") from None
        except requests.exceptions.ProxyError:
            raise JiraError("Cannot connect through the configured proxy. Check corporate network settings.") from None
        except requests.exceptions.Timeout:
            raise JiraError("Jira timed out. Check server availability and network access.") from None
        except requests.exceptions.ConnectionError:
            raise JiraError("Cannot reach Jira. Check the hostname, DNS, VPN, and network access.") from None
        except requests.exceptions.RequestException:
            raise JiraError("The Jira API request failed.") from None
        if response.status_code in (401, 403):
            raise JiraError("Jira denied access. Check the email/API token and board/project permissions.")
        if response.status_code == 404:
            raise JiraError("Sprint not found or inaccessible. Check the sprint ID and your Jira permissions.")
        if response.status_code == 429:
            raise JiraError("Jira rate limit reached. Wait before fetching again.")
        if 300 <= response.status_code < 400:
            raise JiraError("Jira redirected the API request. Check the base URL and authentication setup.")
        if not 200 <= response.status_code < 300:
            raise JiraError(f"Jira returned HTTP {response.status_code}. Check API availability and request permissions.")
        try:
            payload = response.json()
        except ValueError:
            raise JiraError("Jira returned non-JSON data. Check the URL and any SSO proxy.") from None
        if not isinstance(payload, dict):
            raise JiraError("Jira returned an unexpected response shape.")
        return payload

    def issue_row(self, issue: dict, sprint_id: int, flag_field: str) -> dict:
        try:
            fields = issue["fields"]
            status = fields["status"]
            assignee = fields.get("assignee")
            flag_value = fields.get(flag_field) if flag_field else None
            subtask = fields["issuetype"]["subtask"]
            if not isinstance(subtask, bool):
                raise TypeError
            return {
                "Sprint ID": sprint_id,
                "Issue": issue["key"],
                "Summary": fields["summary"],
                "Project": fields["project"]["key"],
                "Status": status["name"],
                "Category": CATEGORY_LABELS.get(status.get("statusCategory", {}).get("key"), "Unknown"),
                "Assignee": assignee["displayName"] if assignee else "Unassigned",
                "Issue type": fields["issuetype"]["name"],
                "Is subtask": subtask,
                "Priority": (fields.get("priority") or {}).get("name", "Not set"),
                "Flagged": bool(flag_value) if flag_field in fields else None,
                "Updated": fields.get("updated"),
                "Jira link": f"{self.url}/browse/{quote(issue['key'], safe='')}",
            }
        except (KeyError, TypeError, AttributeError):
            raise JiraError(f"Jira returned malformed issue data for sprint {sprint_id}.") from None

    def report(self, sprint_id: int, flag_field: str = "") -> SprintReport:
        metadata = self.get(f"sprint/{sprint_id}")
        issues = []
        seen = set()
        start_at = 0
        fields = ["summary", "status", "assignee", "issuetype", "priority", "project", "updated"]
        if flag_field:
            if not re.fullmatch(r"customfield_[0-9]+", flag_field):
                raise JiraError("JIRA_FLAGGED_FIELD must be a Jira field ID such as customfield_10021.")
            fields.append(flag_field)
        while True:
            payload = self.get(
                f"sprint/{sprint_id}/issue",
                startAt=start_at,
                maxResults=100,
                fields=",".join(fields),
            )
            try:
                page = payload["issues"]
                total = payload["total"]
                offset = payload["startAt"]
                if (
                    not isinstance(page, list)
                    or not isinstance(total, int)
                    or total < 0
                    or offset != start_at
                ):
                    raise ValueError
                for issue in page:
                    key = issue["key"]
                    if key in seen:
                        raise JiraError("Issues changed during pagination. Fetch again for a consistent report.")
                    seen.add(key)
                    issues.append(self.issue_row(issue, sprint_id, flag_field))
            except (KeyError, TypeError, ValueError):
                raise JiraError(f"Invalid Jira issue pagination for sprint {sprint_id}.") from None
            start_at += len(page)
            if start_at == total:
                break
            if not page or start_at > total:
                raise JiraError("Issue pagination was incomplete or changed during the fetch. Fetch again.")
        try:
            if (
                metadata["id"] != sprint_id
                or not isinstance(metadata["name"], str)
                or not isinstance(metadata["state"], str)
            ):
                raise ValueError
            return SprintReport(
                sprint_id=sprint_id,
                name=metadata["name"],
                state=metadata["state"],
                goal=metadata.get("goal", ""),
                start=metadata.get("startDate"),
                end=metadata.get("endDate"),
                completed=metadata.get("completeDate"),
                issues=issues,
                fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
        except (KeyError, TypeError, ValueError):
            raise JiraError(f"Invalid sprint metadata for sprint {sprint_id}.") from None
