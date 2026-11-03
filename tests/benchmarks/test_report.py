from dataclasses import replace

from pytask_service.benchmarks.results import RecoveryResult


def test_only_a_complete_successful_plan_is_valid(report):
    assert report.valid
    assert not replace(report, results=()).valid
    assert not replace(report, error_code="case_failed").valid
    assert not replace(report, results=report.results * 2).valid


def test_requested_recovery_must_complete_successfully(report):
    required = replace(report, options=replace(report.options, recovery=True))
    assert not required.valid
    assert not replace(required, recovery=RecoveryResult(False)).valid
    assert replace(
        required, recovery=RecoveryResult(True, 3.0, ("abandoned", "succeeded"))
    ).valid


def test_document_declares_units_and_measurement_boundaries(report):
    document = report.document()
    assert document["schema_version"] == 1
    assert document["planned_cases"] == 1
    assert document["measurement"]["timing_unit"] == "seconds"
    assert document["measurement"]["worker_start_method"] == "spawn"
    assert "first submission" in document["measurement"]["elapsed_boundary"]


def test_finished_timestamp_does_not_change_when_rendering_again(report, monkeypatch):
    from unittest.mock import MagicMock

    clock = MagicMock()
    clock.now.side_effect = AssertionError("rendering must not change the end time")
    monkeypatch.setattr("pytask_service.benchmarks.report.datetime", clock)
    assert report.document()["finished_at"] == report.finished_at
    assert report.document()["finished_at"] == report.finished_at
