"""The extract-load entry point.

Everything here is orchestration, so the tests assert on decisions rather than data: which sources
run, whether backfill mode is on, and whether the staging dataset gets an expiry. All three are
things that fail quietly — a mis-parsed flag or a dropped source still exits 0 and still logs
"load complete".
"""

import runpy
import unittest
import warnings
from unittest import mock

from elt import pipeline
from elt.sources import nyc311, open_meteo


class BuildPipelineTest(unittest.TestCase):
    def test_pipeline_is_pinned_to_name_dataset_and_location(self):
        """The name is how dlt finds its stored cursor; a pod with no local state restores by it."""
        with mock.patch.object(pipeline.dlt, "pipeline") as factory:
            with mock.patch.object(pipeline.dlt.destinations, "bigquery") as destination:
                pipeline.build_pipeline()
        kwargs = factory.call_args.kwargs
        self.assertEqual(kwargs["pipeline_name"], "p03_elt")
        self.assertEqual(kwargs["dataset_name"], pipeline.RAW_DATASET)
        destination.assert_called_once_with(location=pipeline.BQ_LOCATION)


class ReapStagingTablesTest(unittest.TestCase):
    """dlt auto-creates the staging dataset and nothing in Terraform manages its lifecycle."""

    def setUp(self):
        patcher = mock.patch.object(pipeline.bigquery, "Client")
        self.client_cls = patcher.start()
        self.addCleanup(patcher.stop)
        self.client = self.client_cls.return_value

    def test_expiry_is_set_when_absent(self):
        dataset = mock.Mock(default_table_expiration_ms=None)
        self.client.get_dataset.return_value = dataset

        pipeline.reap_staging_tables()

        self.assertEqual(dataset.default_table_expiration_ms, pipeline.STAGING_TABLE_EXPIRY_MS)
        self.client.update_dataset.assert_called_once_with(
            dataset, ["default_table_expiration_ms"]
        )

    def test_expiry_is_corrected_when_wrong(self):
        dataset = mock.Mock(default_table_expiration_ms=123)
        self.client.get_dataset.return_value = dataset

        pipeline.reap_staging_tables()

        self.assertEqual(dataset.default_table_expiration_ms, pipeline.STAGING_TABLE_EXPIRY_MS)
        self.client.update_dataset.assert_called_once()

    def test_already_correct_expiry_is_left_alone(self):
        """An idempotent no-op; rewriting it every run would be a pointless API call per run."""
        dataset = mock.Mock(default_table_expiration_ms=pipeline.STAGING_TABLE_EXPIRY_MS)
        self.client.get_dataset.return_value = dataset

        pipeline.reap_staging_tables()

        self.client.update_dataset.assert_not_called()

    def test_missing_dataset_is_not_an_error(self):
        """On a first run dlt has not created it yet, and that must not fail the load."""
        self.client.get_dataset.side_effect = Exception("404 not found")

        pipeline.reap_staging_tables()

        self.client.update_dataset.assert_not_called()

    def test_expiry_is_seven_days(self):
        self.assertEqual(pipeline.STAGING_TABLE_EXPIRY_MS, 7 * 24 * 60 * 60 * 1000)

    def test_staging_dataset_name_follows_dlt_convention(self):
        self.assertEqual(pipeline.STAGING_DATASET, f"{pipeline.RAW_DATASET}_staging")


class MainTest(unittest.TestCase):
    def setUp(self):
        self.pipeline = mock.Mock()
        patches = {
            "build_pipeline": mock.patch.object(
                pipeline, "build_pipeline", return_value=self.pipeline
            ),
            "reap": mock.patch.object(pipeline, "reap_staging_tables"),
            "nyc311": mock.patch.object(pipeline, "nyc311_source"),
            "open_meteo": mock.patch.object(pipeline, "open_meteo_source"),
        }
        self.mocks = {name: p.start() for name, p in patches.items()}
        for p in patches.values():
            self.addCleanup(p.stop)

    def run_main(self, **env):
        with mock.patch.dict(pipeline.os.environ, env, clear=False):
            # clear=False keeps the ambient environment, so explicitly blank anything not passed.
            for key in ("P03_BACKFILL", "P03_SOURCES"):
                if key not in env:
                    pipeline.os.environ.pop(key, None)
            return pipeline.main()

    # -- source selection ----------------------------------------------------------------------

    def test_default_runs_both_sources(self):
        self.assertEqual(self.run_main(), 0)
        self.mocks["nyc311"].assert_called_once()
        self.mocks["open_meteo"].assert_called_once()
        self.assertEqual(self.pipeline.run.call_count, 2)

    def test_selecting_only_311_skips_weather(self):
        """The backfill loop needs this: re-pulling two years of weather per pass got it throttled."""
        self.run_main(P03_SOURCES="nyc311")
        self.mocks["nyc311"].assert_called_once()
        self.mocks["open_meteo"].assert_not_called()

    def test_selecting_only_weather_skips_311(self):
        self.run_main(P03_SOURCES="open_meteo")
        self.mocks["nyc311"].assert_not_called()
        self.mocks["open_meteo"].assert_called_once()

    def test_selection_tolerates_spaces(self):
        self.run_main(P03_SOURCES=" nyc311 , open_meteo ")
        self.mocks["nyc311"].assert_called_once()
        self.mocks["open_meteo"].assert_called_once()

    def test_unknown_source_names_run_nothing(self):
        self.run_main(P03_SOURCES="typo")
        self.mocks["nyc311"].assert_not_called()
        self.mocks["open_meteo"].assert_not_called()

    def test_empty_selection_falls_back_to_nothing_rather_than_everything(self):
        """An empty string is an explicit choice; treating it as the default would surprise."""
        self.run_main(P03_SOURCES="")
        self.assertEqual(self.pipeline.run.call_count, 0)

    # -- backfill flag -------------------------------------------------------------------------

    def test_backfill_defaults_off(self):
        self.run_main()
        self.assertIs(self.mocks["nyc311"].call_args.kwargs["backfill"], False)

    def test_backfill_accepts_the_documented_spellings(self):
        for value in ("1", "true", "TRUE", "yes", "Yes"):
            with self.subTest(value=value):
                self.mocks["nyc311"].reset_mock()
                self.run_main(P03_BACKFILL=value)
                self.assertIs(self.mocks["nyc311"].call_args.kwargs["backfill"], True)

    def test_other_values_are_not_backfill(self):
        for value in ("0", "false", "no", "maybe", ""):
            with self.subTest(value=value):
                self.mocks["nyc311"].reset_mock()
                self.run_main(P03_BACKFILL=value)
                self.assertIs(self.mocks["nyc311"].call_args.kwargs["backfill"], False)

    def test_backfill_reaches_both_sources(self):
        self.run_main(P03_BACKFILL="true")
        self.assertIs(self.mocks["nyc311"].call_args.kwargs["backfill"], True)
        self.assertIs(self.mocks["open_meteo"].call_args.kwargs["backfill"], True)

    # -- load mechanics ------------------------------------------------------------------------

    def test_loads_go_out_as_parquet(self):
        """JSONL normalisation of 600k rows is a Python loop and took seven minutes."""
        self.run_main()
        for call in self.pipeline.run.call_args_list:
            self.assertEqual(call.kwargs["loader_file_format"], "parquet")

    def test_staging_is_reaped_after_the_loads(self):
        self.run_main()
        self.mocks["reap"].assert_called_once()

    def test_returns_zero_on_success(self):
        self.assertEqual(self.run_main(), 0)

    def test_a_failing_load_propagates(self):
        """The CronJob must see a non-zero exit, and dlt must not commit the cursor."""
        self.pipeline.run.side_effect = RuntimeError("load failed")
        with self.assertRaises(RuntimeError):
            self.run_main()
        self.mocks["reap"].assert_not_called()


class EntryPointTest(unittest.TestCase):
    """`python -m elt.pipeline` is what the CronJob actually runs.

    Worth one test rather than a coverage exclusion: the container's exit status is how Kubernetes
    decides whether to count a failure against backoffLimit, so main() returning a code that never
    reaches sys.exit would make every failed run look successful.

    runpy re-executes the module under a fresh __main__ namespace, but its imports resolve from
    sys.modules, so patching the already-imported dependencies reaches the fresh copy too.
    """

    def test_module_entry_wires_the_return_code_to_the_exit_status(self):
        with mock.patch.object(pipeline.dlt, "pipeline"), mock.patch.object(
            pipeline.bigquery, "Client"
        ), mock.patch.object(nyc311, "nyc311_source"), mock.patch.object(
            open_meteo, "open_meteo_source"
        ), mock.patch.dict(
            pipeline.os.environ, {"P03_SOURCES": ""}
        ):
            with self.assertRaises(SystemExit) as caught:
                # runpy warns that the module is already in sys.modules. That is exactly what makes
                # the patching above work, so the warning is expected rather than a problem.
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    runpy.run_module("elt.pipeline", run_name="__main__")
        self.assertEqual(caught.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
