import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import pandas as pd
from streamlit.testing.v1 import AppTest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from jira_client import JiraClient, JiraError, SprintReport, main_report, parse_sprint_ids, summarize, validate_url
from presentation import breakdown_chart, donut_chart, quality_chart, quality_counts, sprint_quality, status_chart


def api_issue(key="APP-1", category="done", assignee=None):
    return {
        "key": key,
        "fields": {
            "summary": "Example issue",
            "project": {"key": "APP"},
            "status": {"name": "Closed", "statusCategory": {"key": category}},
            "assignee": assignee,
            "issuetype": {"name": "Bug", "subtask": False},
            "priority": None,
            "updated": "2026-10-06",
        },
    }


def sprint_report(sprint_id):
    client = JiraClient("https://example.atlassian.net", "test@example.test", "test-only")
    try:
        rows = [
            client.issue_row(api_issue(), sprint_id, ""),
            client.issue_row(api_issue("APP-2", "indeterminate", {"displayName": "Ramesh"}), sprint_id, ""),
        ]
    finally:
        client.close()
    return SprintReport(
        sprint_id, f"Sprint {sprint_id}", "closed", "Example goal",
        "2026-09-01", "2026-09-10", "2026-09-10", rows, "2026-10-06T10:00:00Z",
    )


def response(payload=None, status=200):
    result = Mock()
    result.status_code = status
    result.json.return_value = payload
    return result


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = JiraClient("https://example.atlassian.net", "test@example.test", "test-only")
        self.addCleanup(self.client.close)

    def test_sprint_ids(self):
        self.assertEqual(parse_sprint_ids("6425, 6419\n6425 6413"), [6425, 6419, 6413])
        for value in ("", "0", "-1", "abc", "12;13", "12.0", "12,,13"):
            with self.subTest(value=value), self.assertRaises(JiraError):
                parse_sprint_ids(value)

    def test_url_validation(self):
        self.assertEqual(validate_url("https://example.atlassian.net/"), "https://example.atlassian.net")
        for value in ("http://host", "https://user:pass@host", "https://host/path", "https://host?x=1", "https://[bad"):
            with self.subTest(value=value), self.assertRaises(JiraError):
                validate_url(value)

    def test_auth_and_request_policy(self):
        with patch.object(self.client.session, "get", return_value=response({})) as get:
            self.client.get("sprint/6425")
        self.assertEqual(self.client.session.auth, ("test@example.test", "test-only"))
        self.assertEqual(get.call_args.args[0], "https://example.atlassian.net/rest/agile/1.0/sprint/6425")
        self.assertEqual(get.call_args.kwargs["timeout"], (10, 45))
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertTrue(self.client.session.verify)

    def test_http_errors(self):
        for status in (401, 403, 404, 429, 302, 500):
            with self.subTest(status=status), patch.object(
                self.client.session, "get", return_value=response({"token": "private"}, status)
            ), self.assertRaises(JiraError) as error:
                self.client.get("sprint/1")
            self.assertNotIn("private", str(error.exception))

    def test_transport_errors_sanitized(self):
        for kind in (
            requests.exceptions.SSLError, requests.exceptions.ProxyError,
            requests.exceptions.Timeout, requests.exceptions.ConnectionError,
            requests.exceptions.RequestException,
        ):
            with self.subTest(kind=kind), patch.object(
                self.client.session, "get", side_effect=kind("private")
            ), self.assertRaises(JiraError) as error:
                self.client.get("sprint/1")
            self.assertNotIn("private", str(error.exception))

    def test_invalid_json(self):
        bad = response()
        bad.json.side_effect = ValueError
        for result in (bad, response([])):
            with patch.object(self.client.session, "get", return_value=result), self.assertRaises(JiraError):
                self.client.get("sprint/1")

    def test_pagination_uses_returned_page_size(self):
        payloads = [
            {"id": 1, "name": "Sprint", "state": "active"},
            {"issues": [api_issue()], "total": 2, "startAt": 0},
            {"issues": [api_issue("APP-2")], "total": 2, "startAt": 1},
        ]
        with patch.object(self.client, "get", side_effect=payloads) as get:
            report = self.client.report(1)
        self.assertEqual(len(report.issues), 2)
        self.assertEqual(get.call_args_list[2].kwargs["startAt"], 1)

    def test_empty_sprint(self):
        with patch.object(self.client, "get", side_effect=[
            {"id": 1, "name": "Sprint", "state": "future"},
            {"issues": [], "total": 0, "startAt": 0},
        ]):
            report = self.client.report(1)
        self.assertEqual(summarize(report)["Total issues"], 0)
        self.assertIsNone(summarize(report)["Done (%)"])
        self.assertIsNone(summarize(report)["Flagged"])

    def test_incomplete_duplicate_and_invalid_pages_fail(self):
        for second in (
            {"issues": [], "total": 2, "startAt": 1},
            {"issues": [api_issue()], "total": 2, "startAt": 1},
            {"issues": [api_issue("APP-2")], "total": 2, "startAt": 0},
        ):
            with self.subTest(second=second), patch.object(self.client, "get", side_effect=[
                {"id": 1, "name": "Sprint", "state": "active"},
                {"issues": [api_issue()], "total": 2, "startAt": 0},
                second,
            ]), self.assertRaises(JiraError):
                self.client.report(1)

    def test_mapping_done_and_unknown(self):
        self.assertEqual(self.client.issue_row(api_issue(), 1, "")["Category"], "Done")
        row = self.client.issue_row(api_issue(category="unexpected"), 1, "")
        self.assertEqual(row["Category"], "Unknown")
        self.assertEqual(row["Assignee"], "Unassigned")
        self.assertIsNone(row["Flagged"])
        self.assertEqual(row["Priority"], "Not set")
        with self.assertRaises(JiraError):
            self.client.issue_row({"key": "bad"}, 1, "")

    def test_flagged_field_and_summary(self):
        issue = api_issue()
        issue["fields"]["customfield_123"] = [{"value": "Impediment"}]
        row = self.client.issue_row(issue, 1, "customfield_123")
        self.assertTrue(row["Flagged"])
        report = sprint_report(1)
        result = summarize(report)
        self.assertEqual(result["Done"], 1)
        self.assertEqual(result["Remaining"], 1)
        self.assertEqual(result["Done (%)"], 50)
        self.assertEqual(result["Unassigned"], 1)
        self.assertIsNone(result["Flagged"])
        with patch.object(self.client, "get", return_value={}), self.assertRaises(JiraError):
            self.client.report(1, "not-a-field")


class PresentationTests(unittest.TestCase):
    def test_report_scope_excludes_other_types_and_custom_subtasks(self):
        from dataclasses import replace

        report = sprint_report(1)
        template = report.issues[0]
        rows = [
            dict(template, **{"Issue": f"APP-{index}", "Issue type": name, "Is subtask": subtask})
            for index, (name, subtask) in enumerate([
                (" Story ", False), ("BUG", False), ("Defect", False),
                ("Task", False), ("Sub-task", True), ("Story", True),
            ])
        ]
        source = replace(report, issues=rows)
        scoped = main_report(source)
        self.assertEqual(len(scoped.issues), 3)
        self.assertEqual(len(source.issues), 6)
        self.assertEqual(summarize(scoped)["Total issues"], 3)
        self.assertEqual(summarize(scoped)["Done (%)"], 100)

    def test_assignee_chart_has_visible_count_labels(self):
        issues = pd.DataFrame(sprint_report(1).issues)
        spec = breakdown_chart(issues, "Assignee", show_labels=True).to_dict()
        self.assertEqual(spec["layer"][1]["mark"]["type"], "text")
        self.assertEqual(spec["layer"][1]["encoding"]["text"]["field"], "Issues")
        self.assertEqual(spec["layer"][1]["encoding"]["text"]["format"], "d")
        self.assertGreater(spec["layer"][0]["encoding"]["x"]["scale"]["domain"][1], 1)

    def test_exact_quality_types_and_statuses(self):
        issues = pd.DataFrame([
            {"Issue type": " Bug ", "Category": "Done"},
            {"Issue type": "DEFECT", "Category": "In progress"},
            {"Issue type": "Bug", "Category": "Unknown"},
            {"Issue type": "Bug investigation", "Category": "To do"},
            {"Issue type": "Story", "Category": "Done"},
        ])
        self.assertEqual(quality_counts(issues), {
            "Bugs": 2, "Defects": 1, "Open bugs / defects": 2, "Resolved bugs / defects": 1,
        })

    def test_quality_counts_empty(self):
        issues = pd.DataFrame(columns=["Issue type", "Category"])
        self.assertEqual(sum(quality_counts(issues).values()), 0)

    def test_chart_specs_and_duplicate_sprint_names(self):
        reports = [sprint_report(6425), sprint_report(6419)]
        data = sprint_quality(reports)
        self.assertEqual(data["Bugs"].tolist(), [2, 2])
        self.assertEqual(data["Open bugs / defects"].tolist(), [1, 1])
        self.assertEqual(len(set(data["Sprint"])), 2)
        issues = pd.DataFrame(reports[0].issues)
        comparison = pd.DataFrame([summarize(report) for report in reports])
        charts = [
            status_chart(comparison), quality_chart(reports),
            donut_chart(issues), breakdown_chart(issues, "Priority"),
        ]
        for chart in charts:
            self.assertTrue(chart.to_dict()["encoding"]["tooltip"])


class DashboardTests(unittest.TestCase):
    def app(self, token="ui-test"):
        app = AppTest.from_file(str(PROJECT_DIR / "streamlit_app.py"), default_timeout=15)
        app.secrets["JIRA_URL"] = "https://example.atlassian.net"
        app.secrets["JIRA_EMAIL"] = "test@example.test"
        app.secrets["JIRA_API_TOKEN"] = token
        return app

    def test_missing_configuration_no_network(self):
        with patch("jira_client.JiraClient.report") as fetch:
            app = self.app("").run()
        self.assertFalse(app.exception)
        self.assertIn("JIRA_API_TOKEN", app.error[0].value)
        fetch.assert_not_called()

    def test_fetch_comparison_filter_and_updated_ids(self):
        with patch("jira_client.JiraClient.report", side_effect=lambda sprint_id, field: sprint_report(sprint_id)) as fetch:
            app = self.app("ui-success").run()
            self.assertFalse(app.exception)
            fetch.assert_not_called()
            app.text_area[0].set_value("6425, 6419").run()
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.dataframe[0].value), 2)
            self.assertEqual(fetch.call_count, 2)
            self.assertTrue(any(metric.label == "Completion" and metric.value == "50.0%" for metric in app.metric))
            self.assertTrue(any(metric.label == "Bugs" and metric.value == "2" for metric in app.metric))
            self.assertTrue(any(metric.label == "Open bugs / defects" and metric.value == "1" for metric in app.metric))
            self.assertGreaterEqual(len(app.get("vega_lite_chart")), 7)
            app.multiselect[0].set_value(["Closed"]).run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.dataframe[1].value), 2)
            app.multiselect[1].set_value(["Unassigned"]).run()
            self.assertEqual(len(app.dataframe[1].value), 1)
            self.assertEqual(fetch.call_count, 2)
            app.text_area[0].set_value("6681").run()
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.dataframe[0].value["Sprint ID"].tolist(), [6681])
            self.assertEqual(fetch.call_count, 3)

    def test_invalid_ids_no_fetch(self):
        with patch("jira_client.JiraClient.report") as fetch:
            app = self.app().run()
            app.text_area[0].set_value("abc").run()
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        fetch.assert_not_called()

    def test_partial_and_total_failure(self):
        def fetch(sprint_id, field):
            if sprint_id == 6419:
                raise JiraError("Jira denied access.")
            return sprint_report(sprint_id)

        with patch("jira_client.JiraClient.report", side_effect=fetch):
            app = self.app("ui-partial").run()
            app.text_area[0].set_value("6425, 6419").run()
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.dataframe[0].value), 1)
            self.assertTrue(any("Partial results" in warning.value for warning in app.warning))
            app.text_area[0].set_value("6419").run()
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.dataframe)
            self.assertTrue(any("No sprint reports" in warning.value for warning in app.warning))

    def test_empty_sprint_rendering(self):
        empty = SprintReport(1, "Empty sprint", "future", "", None, None, None, [], "2026-10-06")
        with patch("jira_client.JiraClient.report", return_value=empty):
            app = self.app("ui-empty").run()
            app.text_area[0].set_value("1").run()
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any(metric.label == "Completion" and metric.value == "N/A" for metric in app.metric))
        self.assertTrue(any("no issues" in info.value for info in app.info))

    def test_subtasks_hidden_and_separate_without_refetch(self):
        from dataclasses import replace

        report = sprint_report(1)
        subtask = dict(report.issues[0], **{
            "Issue": "APP-SUB", "Issue type": "Custom child", "Is subtask": True,
        })
        task = dict(report.issues[0], **{"Issue": "APP-TASK", "Issue type": "Task"})
        report = replace(report, issues=report.issues + [subtask, task])
        with patch("jira_client.JiraClient.report", return_value=report) as fetch:
            app = self.app("ui-subtask-scope").run()
            app.text_area[0].set_value("1").run()
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.dataframe[0].value["Total issues"].tolist(), [2])
            self.assertEqual(app.dataframe[1].value["Issue"].tolist(), ["APP-1", "APP-2"])
            self.assertFalse(app.checkbox[0].value)
            self.assertEqual(len(app.download_button), 2)
            self.assertTrue(any("Issues by assignee" in item.value for item in app.markdown))
            self.assertFalse(any("Workload by assignee" in item.value for item in app.markdown))
            app.checkbox[0].check().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.dataframe[2].value["Issue"].tolist(), ["APP-SUB"])
            self.assertEqual(app.dataframe[0].value["Total issues"].tolist(), [2])
            self.assertEqual(len(app.download_button), 3)
            app.checkbox[0].uncheck().run()
            self.assertEqual(len(app.dataframe), 2)
            self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
