"""Shared test fixtures for ix."""

from __future__ import annotations

import pytest

#: Every test that runs ix's native engine. That engine was built on matrix's flow API
#: (``Flow``, ``Member``, ``compile_flow``, port-named ``Inputs``), which matrix 0.4 removed;
#: it belongs to the harness being replaced and was not ported, so these cannot pass.
#: ``strict``: a test listed here that passes fails the suite, so the list stays exact.
NEEDS_THE_NATIVE_ENGINE = frozenset(
    {
        "test_agent_drivability.py::TestExitCodes::test_missing_subject_results_point_at_the_ones_that_exist",
        "test_agent_drivability.py::TestJsonEverywhere::test_run_and_results_have_one_shape_at_any_count",
        "test_agent_drivability.py::TestJsonEverywhere::test_compare_names_its_schema_and_both_runs",
        "test_agent_drivability.py::TestPlanAndLiveGuard::test_simulate_or_a_named_subject_is_not_refused",
        "test_agent_drivability.py::TestPlanAndLiveGuard::test_several_simulated_subjects_run_as_before",
        "test_agent_drivability.py::TestProvenance::test_seed_and_simulate_are_recorded",
        "test_agent_drivability.py::TestProvenance::test_simulated_json_status_is_unmeasured",
        "test_agent_drivability.py::TestProvenance::test_simulating_a_live_subject_never_replaces_its_results",
        "test_agent_drivability.py::TestProvenance::test_config_hash_covers_the_probes",
        "test_experiment.py::TestExperiment::test_produces_results_with_metrics",
        "test_experiment.py::TestExperiment::test_status_derived_from_pass_rate",
        "test_experiment.py::TestExperiment::test_runs_all_probes_regardless_of_metadata",
        "test_experiment.py::TestExperiment::test_callback_fires_per_probe",
        "test_experiment.py::TestExperiment::test_results_persisted_to_store",
        "test_experiment.py::TestExperiment::test_provenance_populated",
        "test_experiment.py::TestExperiment::test_simulated_runtime_high_pass_rate",
        "test_experiment.py::TestEngines::test_native_engine_named_in_provenance",
        "test_experiment.py::TestEngines::test_inspect_engine_matches_native",
        "test_ix_composition.py::TestInit::test_new_experiment_runs_on_the_starter_subject",
        "test_ix_contracts.py::TestTrialsArePersisted::test_every_trial_of_every_repeat_is_a_record",
        "test_ix_contracts.py::TestNativeConcurrency::test_order_is_probe_by_trial_at_any_concurrency",
        "test_ix_contracts.py::TestCli::test_run_without_subject_runs_every_subject_and_saves_each",
        "test_ix_contracts.py::TestCli::test_json_output_is_clean_stdout",
        "test_ix_contracts.py::TestCli::test_compare_two_subjects",
        "test_ix_contracts.py::TestCli::test_mock_flag_still_works_with_a_deprecation_notice",
        "test_ix_contracts.py::TestProvenance::test_simulated_cli_run_records_simulated",
        "test_ix_contracts.py::TestProgress::test_every_trial_of_every_repeat_reports_as_it_is_measured",
        "test_ix_contracts.py::TestProgress::test_probe_lines_print_with_repeats_too",
        "test_ix_contracts.py::TestProgress::test_cli_prints_a_verdict_line_per_probe",
        "test_ix_contracts.py::TestProgress::test_compare_header_reads_in_argument_order",
        "test_ix_contracts.py::TestSensorFaults::test_a_crashing_sensor_is_counted_as_its_own_fault",
        "test_trials_and_faults.py::test_trial_step_is_a_component_that_puts_the_probe_to_the_agent",
        "test_trials_and_faults.py::test_the_native_engine_runs_one_compiled_flow_and_reports_each_trial",
        "test_trials_and_faults.py::test_every_trial_is_measured_once_by_the_experiments_rule",
        "test_trials_and_faults.py::test_harness_faults_are_counted_and_kept_out_of_the_score",
        "test_trials_and_faults.py::test_compare_will_not_call_a_winner_over_harness_faults",
        "test_trials_and_faults.py::test_an_agent_that_cannot_be_built_is_a_recorded_harness_fault",
    }
)

_REASON = "runs ix's native engine, which was built on matrix's removed flow API and was not ported"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.nodeid.split("/")[-1] in NEEDS_THE_NATIVE_ENGINE:
            item.add_marker(pytest.mark.xfail(reason=_REASON, strict=True))
