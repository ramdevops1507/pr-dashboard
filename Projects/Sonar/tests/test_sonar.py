import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from streamlit.testing.v1 import AppTest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from sonar_client import METRICS, ProjectSnapshot, SonarClient, SonarError, format_measure, validate_url


def response(payload, status=200):
    result = Mock()
    result.status_code = status
    result.json.return_value = payload
    return result


def snapshot(key="BootsApp-iOS", gate="OK"):
    return ProjectSnapshot(
        key=key,
        name=key,
        measures={"bugs": 0, "coverage": 82.5, "duplicated_lines_density": 2.1},
        gate=gate,
        conditions=[
            {"metricKey": "new_coverage", "status": "OK", "actualValue": "82.5", "errorThreshold": "80"}
        ],
        analysis_date="2026-10-04T08:00:00+0000",
        fetched_at="2026-10-04T09:00:00+00:00",
    )


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = SonarClient("https://sonar.example.test", "test-only-token")
        self.addCleanup(self.client.close)

    def test_url_and_token_validation(self):
        self.assertEqual(validate_url("https://sonar.example.test/sonar/"), "https://sonar.example.test/sonar")
        for url in (
            "http://sonar.example.test",
            "https://user:pass@host",
            "https://host?token=x",
            "https://host/#x",
            "https://[invalid",
            "https://host:invalid",
        ):
            with self.subTest(url=url), self.assertRaises(SonarError):
                validate_url(url)
        with self.assertRaises(SonarError):
            SonarClient("https://sonar.example.test", " ")

    def test_http_errors_are_explicit_and_do_not_expose_response(self):
        for status in (401, 403, 404, 302, 500):
            with self.subTest(status=status):
                with patch.object(self.client.session, "get", return_value=response({"secret": "sensitive"}, status)):
                    with self.assertRaises(SonarError) as error:
                        self.client.get("measures/component", component="project")
                    self.assertNotIn("sensitive", str(error.exception))

    def test_transport_errors_are_sanitized(self):
        for exception in (
            requests.exceptions.SSLError("test-only-token"),
            requests.exceptions.Timeout("test-only-token"),
            requests.exceptions.ConnectionError("test-only-token"),
            requests.exceptions.RequestException("test-only-token"),
        ):
            with self.subTest(exception=type(exception).__name__):
                with patch.object(self.client.session, "get", side_effect=exception):
                    with self.assertRaises(SonarError) as error:
                        self.client.get("metrics/search")
                    self.assertNotIn("test-only-token", str(error.exception))

    def test_non_json_and_wrong_shape(self):
        bad_json = response(None)
        bad_json.json.side_effect = ValueError("HTML login page")
        for result in (bad_json, response([])):
            with patch.object(self.client.session, "get", return_value=result), self.assertRaises(SonarError):
                self.client.get("metrics/search")

    def test_request_auth_timeout_and_redirect_policy(self):
        with patch.object(self.client.session, "get", return_value=response({})) as get:
            self.client.get("metrics/search", ps=500)
        self.assertEqual(self.client.session.auth, ("test-only-token", ""))
        self.assertEqual(get.call_args.kwargs["timeout"], (10, 45))
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertTrue(self.client.session.verify)

    def test_catalog_pagination(self):
        with patch.object(self.client, "get", side_effect=[
            {"metrics": [{"key": "bugs"}], "total": 501},
            {"metrics": [{"key": "coverage"}], "total": 501},
        ]) as get:
            self.assertEqual(self.client.available_metrics(), {"bugs", "coverage"})
        self.assertEqual(get.call_args_list[1].kwargs["p"], 2)

    def test_invalid_catalog(self):
        for payload in ({}, {"metrics": None, "total": 1}, {"metrics": [], "total": 501}):
            with patch.object(self.client, "get", return_value=payload), self.assertRaises(SonarError):
                self.client.available_metrics()

    def test_snapshot_branch_and_missing_metrics(self):
        payloads = [
            {"component": {"key": "ios", "name": "iOS", "measures": [
                {"metric": "coverage", "value": "82.5"}, {"metric": "bugs", "value": "0"}
            ]}},
            {"projectStatus": {"status": "ERROR", "conditions": []}},
            {"analyses": [{"date": "2026-10-04"}]},
        ]
        with patch.object(self.client, "get", side_effect=payloads) as get:
            result = self.client.snapshot("ios", "release/1", {"bugs", "coverage"})
        self.assertEqual(result.gate, "ERROR")
        self.assertEqual(result.measures["bugs"], 0)
        self.assertNotIn("vulnerabilities", result.measures)
        self.assertEqual(get.call_args_list[0].kwargs["metricKeys"], "bugs,coverage")
        for call in get.call_args_list:
            self.assertEqual(call.kwargs["branch"], "release/1")

    def test_main_branch_no_analysis(self):
        with patch.object(self.client, "get", side_effect=[
            {"component": {"key": "ios", "name": "iOS", "measures": []}},
            {"projectStatus": {"status": "NONE"}},
            {"analyses": []},
        ]) as get:
            result = self.client.snapshot("ios", "", set(METRICS))
        self.assertIsNone(result.analysis_date)
        self.assertEqual(result.gate, "NONE")
        for call in get.call_args_list:
            self.assertNotIn("branch", call.kwargs)

    def test_invalid_measures_are_errors(self):
        with patch.object(self.client, "get", side_effect=[
            {"component": {"key": "ios", "name": "iOS", "measures": [{"metric": "bugs", "value": "NaN"}]}},
            {"projectStatus": {"status": "OK"}},
            {"analyses": []},
        ]), self.assertRaises(SonarError):
            self.client.snapshot("ios", "", set(METRICS))
        with self.assertRaises(SonarError):
            self.client.snapshot("ios", "", set())

    def test_malformed_gate_conditions_are_errors(self):
        with patch.object(self.client, "get", side_effect=[
            {"component": {"key": "ios", "name": "iOS", "measures": []}},
            {"projectStatus": {"status": "OK", "conditions": ["invalid"]}},
            {"analyses": []},
        ]), self.assertRaises(SonarError):
            self.client.snapshot("ios", "", set(METRICS))

    def test_formatting_and_encoded_link(self):
        self.assertEqual(format_measure("bugs", None), "N/A")
        self.assertEqual(format_measure("bugs", 0), "0")
        self.assertEqual(format_measure("coverage", 82.54), "82.5%")
        self.assertEqual(format_measure("ncloc", 12345), "12,345")
        url = self.client.project_url("ios & android", "release/1")
        self.assertIn("id=ios+%26+android", url)
        self.assertIn("branch=release%2F1", url)


class DashboardTests(unittest.TestCase):
    def app(self, token="ui-test-token"):
        app = AppTest.from_file(str(PROJECT_DIR / "streamlit_app.py"), default_timeout=15)
        app.secrets["SONAR_TOKEN"] = token
        app.secrets["SONAR_URL"] = "https://sonar.example.test"
        return app

    def test_missing_token_does_not_call_api(self):
        with patch("sonar_client.SonarClient.snapshot") as fetch:
            app = self.app("").run()
        self.assertFalse(app.exception)
        self.assertIn("SONAR_TOKEN", app.error[0].value)
        fetch.assert_not_called()

    def test_both_projects_metrics_filter_branch_and_refresh(self):
        with patch("sonar_client.SonarClient.available_metrics", return_value=set(METRICS)), patch(
            "sonar_client.SonarClient.snapshot",
            side_effect=lambda key, branch, available: snapshot(key),
        ) as fetch:
            app = self.app("ui-test-success").run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.dataframe[0].value), 2)
            self.assertTrue(any(metric.label == "Bugs" and metric.value == "0" for metric in app.metric))
            self.assertTrue(any(metric.value == "N/A" for metric in app.metric))
            self.assertEqual(len(app.success), 2)
            self.assertEqual(fetch.call_count, 2)
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertEqual(fetch.call_count, 4)
            app.multiselect[0].set_value(["BootsApp-iOS"]).run()
            self.assertEqual(len(app.dataframe[0].value), 1)
            self.assertEqual(fetch.call_count, 4)
            app.text_input[0].set_value("release/1").run()
            self.assertFalse(app.exception)
            self.assertEqual(fetch.call_args.args[1], "release/1")
            self.assertEqual(fetch.call_count, 5)
            app.multiselect[0].set_value([]).run()
            self.assertFalse(app.exception)
            self.assertIn("Select at least one", app.info[0].value)

    def test_partial_failure_is_not_a_pass(self):
        def fetch(key, branch, available):
            if key == "BootsApp-android":
                raise SonarError("SonarQube denied access.")
            return snapshot(key, "ERROR")

        with patch("sonar_client.SonarClient.available_metrics", return_value=set(METRICS)), patch(
            "sonar_client.SonarClient.snapshot", side_effect=fetch
        ):
            app = self.app("ui-test-partial").run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe[0].value), 1)
        self.assertTrue(any("Partial results" in warning.value for warning in app.warning))
        self.assertTrue(any("Quality gate: Failed" in error.value for error in app.error))
        self.assertFalse(app.success)

    def test_total_failure_and_unknown_gate(self):
        with patch("sonar_client.SonarClient.available_metrics", side_effect=SonarError("Cannot reach SonarQube.")):
            app = self.app("ui-test-failure").run()
        self.assertFalse(app.exception)
        self.assertFalse(app.dataframe)
        self.assertEqual(len(app.error), 2)
        with patch("sonar_client.SonarClient.available_metrics", return_value=set(METRICS)), patch(
            "sonar_client.SonarClient.snapshot", return_value=snapshot(gate="NONE")
        ):
            app = self.app("ui-test-unknown").run()
        self.assertFalse(app.exception)
        self.assertFalse(app.success)
        self.assertTrue(any("not a confirmed pass" in warning.value for warning in app.warning))


if __name__ == "__main__":
    unittest.main()
