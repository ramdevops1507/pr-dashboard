from dataclasses import dataclass
from math import isfinite
from urllib.parse import urlencode, urlsplit

import requests


METRICS = {
    "bugs": "Bugs",
    "vulnerabilities": "Vulnerabilities",
    "code_smells": "Code smells",
    "security_hotspots": "Security hotspots",
    "coverage": "Coverage",
    "duplicated_lines_density": "Duplication",
    "ncloc": "Lines of code",
    "sqale_index": "Technical debt (minutes)",
}


class SonarError(Exception):
    """A configuration, transport, or API error safe to display to users."""


@dataclass(frozen=True)
class ProjectSnapshot:
    key: str
    name: str
    measures: dict[str, float]
    gate: str
    conditions: list[dict]
    analysis_date: str | None
    fetched_at: str


def validate_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        parsed.port
    except ValueError:
        raise SonarError("SONAR_URL contains an invalid hostname or port.") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise SonarError("SONAR_URL must be an HTTPS server URL without credentials, query, or fragment.")
    return url.rstrip("/")


def format_measure(metric: str, value: float | None) -> str:
    if value is None:
        return "N/A"
    if metric in {"coverage", "duplicated_lines_density"}:
        return f"{value:.1f}%"
    return f"{value:,.0f}"


class SonarClient:
    def __init__(self, url: str, token: str):
        self.url = validate_url(url)
        if not token.strip():
            raise SonarError("SONAR_TOKEN is missing or empty.")
        self.session = requests.Session()
        self.session.auth = (token, "")
        self.session.headers["Accept"] = "application/json"

    def close(self) -> None:
        self.session.close()

    def get(self, endpoint: str, **params) -> dict:
        try:
            response = self.session.get(
                f"{self.url}/api/{endpoint}",
                params=params,
                timeout=(10, 45),
                allow_redirects=False,
            )
        except requests.exceptions.SSLError:
            raise SonarError(
                "TLS certificate validation failed. Configure your approved corporate CA "
                "with REQUESTS_CA_BUNDLE; do not disable certificate verification."
            ) from None
        except requests.exceptions.Timeout:
            raise SonarError("SonarQube timed out. Check server availability and VPN connectivity.") from None
        except requests.exceptions.ConnectionError:
            raise SonarError("Cannot reach SonarQube. Check DNS, VPN, and network access.") from None
        except requests.exceptions.RequestException:
            raise SonarError(f"Request to {endpoint} failed.") from None
        if response.status_code in (401, 403):
            raise SonarError("SonarQube denied access. Check the token and project Browse permission.")
        if response.status_code == 404:
            raise SonarError(
                f"{endpoint} returned 404. Check the server URL, project key, and branch; "
                "the project key may differ from its display name."
            )
        if 300 <= response.status_code < 400:
            raise SonarError("SonarQube redirected the API request. Check the URL and SSO/proxy configuration.")
        if not 200 <= response.status_code < 300:
            raise SonarError(f"SonarQube {endpoint} returned HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError:
            raise SonarError("SonarQube returned non-JSON data. Check the URL and SSO/proxy configuration.") from None
        if not isinstance(payload, dict):
            raise SonarError(f"Unexpected response shape from {endpoint}.")
        return payload

    def available_metrics(self) -> set[str]:
        result = set()
        page = 1
        while True:
            payload = self.get("metrics/search", p=page, ps=500)
            try:
                metrics = payload["metrics"]
                total = int(payload["total"])
                if not isinstance(metrics, list):
                    raise TypeError
                result.update(metric["key"] for metric in metrics)
            except (KeyError, TypeError, ValueError):
                raise SonarError("Invalid metric catalog returned by SonarQube.") from None
            if page * 500 >= total:
                return result
            if not metrics:
                raise SonarError("SonarQube metric pagination ended before the reported total.")
            page += 1

    def snapshot(self, key: str, branch: str, available: set[str]) -> ProjectSnapshot:
        from datetime import datetime, timezone

        params = {"component": key}
        if branch:
            params["branch"] = branch
        supported = [metric for metric in METRICS if metric in available]
        if not supported:
            raise SonarError("None of the dashboard's metrics are supported by this SonarQube server.")
        measures_payload = self.get(
            "measures/component", **params, metricKeys=",".join(supported)
        )
        gate_params = {"projectKey": key}
        analysis_params = {"project": key, "ps": 1}
        if branch:
            gate_params["branch"] = branch
            analysis_params["branch"] = branch
        gate_payload = self.get("qualitygates/project_status", **gate_params)
        analyses_payload = self.get("project_analyses/search", **analysis_params)
        try:
            component = measures_payload["component"]
            measures = {}
            for measure in component["measures"]:
                value = float(measure["value"])
                if not isfinite(value):
                    raise ValueError
                measures[measure["metric"]] = value
            gate = gate_payload["projectStatus"]
            conditions = gate.get("conditions", [])
            analyses = analyses_payload["analyses"]
            if (
                not isinstance(conditions, list)
                or any(not isinstance(condition, dict) for condition in conditions)
                or not isinstance(analyses, list)
                or not isinstance(gate["status"], str)
                or not isinstance(component["key"], str)
                or not isinstance(component["name"], str)
            ):
                raise TypeError
            return ProjectSnapshot(
                key=component["key"],
                name=component["name"],
                measures=measures,
                gate=gate["status"],
                conditions=conditions,
                analysis_date=analyses[0]["date"] if analyses else None,
                fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
        except (KeyError, TypeError, ValueError):
            raise SonarError(f"Invalid project data returned for {key}.") from None

    def project_url(self, key: str, branch: str = "") -> str:
        params = {"id": key}
        if branch:
            params["branch"] = branch
        return f"{self.url}/dashboard?{urlencode(params)}"
