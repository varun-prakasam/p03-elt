"""The credential and connectivity probe.

Its whole job is to answer "which identity am I, and can it see the dataset" before a real load
finds out the hard way. Local runs authenticate as the broad terraform-deployer through impersonated
ADC while the CronJob runs as the much narrower wl-p03-elt, so the identity line is the useful
output — it is how you catch a pod that silently fell back to the node service account.
"""

import io
import runpy
import unittest
import warnings
from contextlib import redirect_stdout
from unittest import mock

from elt import healthcheck


class HealthcheckTest(unittest.TestCase):
    def setUp(self):
        self.credentials = mock.Mock()
        self.credentials.service_account_email = "wl-p03-elt@varun-data-engineering.iam.gserviceaccount.com"

        auth = mock.patch.object(
            healthcheck.google.auth, "default", return_value=(self.credentials, "varun-data-engineering")
        )
        self.auth = auth.start()
        self.addCleanup(auth.stop)

        client_patch = mock.patch.object(healthcheck.bigquery, "Client")
        self.client_cls = client_patch.start()
        self.addCleanup(client_patch.stop)

        self.client = self.client_cls.return_value
        self.client.query.return_value.result.return_value = iter([{"who": "wl-p03-elt@…"}])
        self.client.get_dataset.return_value = mock.Mock(location="us-central1")
        self.client.list_tables.return_value = [
            mock.Mock(table_id="service_requests"),
            mock.Mock(table_id="weather_daily"),
        ]

    def run_main(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = healthcheck.main()
        return code, buffer.getvalue()

    def test_returns_zero_when_everything_resolves(self):
        code, _ = self.run_main()
        self.assertEqual(code, 0)

    def test_reports_the_identity_it_authenticated_as(self):
        _, output = self.run_main()
        self.assertIn("wl-p03-elt@", output)

    def test_reports_the_detected_project(self):
        _, output = self.run_main()
        self.assertIn("varun-data-engineering", output)

    def test_unknown_identity_does_not_crash(self):
        """User credentials have no service_account_email; the probe should still report."""
        del self.credentials.service_account_email
        code, output = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("unknown", output)

    def test_client_is_pinned_to_the_dataset_location(self):
        """A job submitted against the wrong location reports the dataset as simply not found."""
        self.run_main()
        self.client_cls.assert_called_once_with(
            project=healthcheck.PROJECT_ID, location=healthcheck.BQ_LOCATION
        )

    def test_reports_the_dataset_location(self):
        _, output = self.run_main()
        self.assertIn("us-central1", output)

    def test_lists_existing_tables(self):
        _, output = self.run_main()
        self.assertIn("service_requests", output)
        self.assertIn("weather_daily", output)

    def test_empty_dataset_reads_as_none_yet(self):
        """A first run has no tables, which is expected rather than an error."""
        self.client.list_tables.return_value = []
        _, output = self.run_main()
        self.assertIn("none yet", output)

    def test_queries_the_session_user(self):
        self.run_main()
        self.assertIn("session_user()", self.client.query.call_args.args[0])

    def test_missing_dataset_surfaces(self):
        """Unlike the staging reaper, this is a probe — a missing raw dataset is a real failure."""
        self.client.get_dataset.side_effect = Exception("404 Not found")
        with self.assertRaises(Exception):
            self.run_main()

    def test_module_entry_wires_the_return_code_to_the_exit_status(self):
        """`kubectl exec … -m elt.healthcheck` is only useful if a failure exits non-zero."""
        buffer = io.StringIO()
        with redirect_stdout(buffer), self.assertRaises(SystemExit) as caught:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                runpy.run_module("elt.healthcheck", run_name="__main__")
        self.assertEqual(caught.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
