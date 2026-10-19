"""Telemetry on the standard variables: a library uses the API, an application installs the
SDK, and export asked for without what does it is a warning, never silence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import matrix
from matrix import MatrixConfig, compose, configure_telemetry
from matrix.adapters._in.cli import main
from matrix.composition import telemetry

pytest.importorskip("opentelemetry.sdk")

_OTEL = (
    "OTEL_SDK_DISABLED",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    "OTEL_TRACES_EXPORTER",
    "OTEL_METRICS_EXPORTER",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
)


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """No OTEL_* from the shell, and a record of every global provider installed."""
    from opentelemetry import metrics, trace

    for variable in _OTEL:
        monkeypatch.delenv(variable, raising=False)
    installed: list[str] = []
    monkeypatch.setattr(trace, "set_tracer_provider", lambda p: installed.append("traces"))
    monkeypatch.setattr(metrics, "set_meter_provider", lambda p: installed.append("metrics"))
    return installed


@pytest.mark.parametrize(
    ("environment", "asks"),
    [
        ({}, None),
        ({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}, "OTEL_EXPORTER_OTLP_ENDPOINT"),
        ({"OTEL_TRACES_EXPORTER": "console"}, "OTEL_TRACES_EXPORTER"),
        ({"OTEL_TRACES_EXPORTER": "none", "OTEL_METRICS_EXPORTER": "none"}, None),
        ({"OTEL_TRACES_EXPORTER": "console", "OTEL_SDK_DISABLED": "true"}, None),
    ],
)
def test_the_standard_variables_say_whether_to_export(
    environment: dict[str, str], asks: str | None
) -> None:
    assert telemetry.requested(environment) == asks


def test_the_library_installs_no_provider_whatever_the_variables_say(
    monkeypatch: pytest.MonkeyPatch, clean: list[str]
) -> None:
    from matrix.adapters._out.observers.otel import OtelObserver

    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console")
    container = compose(MatrixConfig())
    assert any(isinstance(o, OtelObserver) for o in container.observers)  # the API, attached
    assert clean == []  # and no SDK installed: that is the application's to do


def test_with_nothing_asked_no_otel_observer_is_attached() -> None:
    from matrix.adapters._out.observers.otel import OtelObserver

    assert not any(isinstance(o, OtelObserver) for o in compose(MatrixConfig()).observers)


def test_an_application_installs_the_sdk_from_the_variables(
    monkeypatch: pytest.MonkeyPatch, clean: list[str]
) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    installed = configure_telemetry()
    try:
        assert installed.enabled and installed.exporters == ("traces:console",)
        assert clean == ["traces"]
    finally:
        installed.shutdown()


def test_disabled_means_nothing_even_when_asked(
    monkeypatch: pytest.MonkeyPatch, clean: list[str]
) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert not configure_telemetry(console=True).enabled and clean == []


def test_export_asked_for_without_the_extra_is_a_warning_naming_the_install(
    monkeypatch: pytest.MonkeyPatch, clean: list[str]
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.setattr(telemetry, "_installed", lambda module=telemetry._SDK: False)
    (warning,) = telemetry.warnings()
    assert (warning.path, warning.kind) == ("OTEL_EXPORTER_OTLP_ENDPOINT", "extra_missing")
    assert 'uv add "matrix[otel] @ git+' in warning.fix and "pip" not in warning.fix
    installed = configure_telemetry()
    assert not installed.enabled and installed.warnings == (warning,) and clean == []
    assert compose(MatrixConfig()).warnings == (warning,)


def test_what_matrix_does_not_install_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "zipkin")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/json")
    monkeypatch.setattr(telemetry, "_installed", lambda module=telemetry._SDK: True)
    found = {(w.path, w.kind, w.got) for w in telemetry.warnings()}
    assert found == {
        ("OTEL_TRACES_EXPORTER", "invalid_value", "zipkin"),
        ("OTEL_EXPORTER_OTLP_PROTOCOL", "invalid_value", "http/json"),
    }


def test_otlp_follows_the_protocol_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """http/protobuf is the spec's default; grpc when the variable says so."""
    import sys
    import types

    made: list[str] = []
    for module, name, label in (
        ("opentelemetry.exporter.otlp.proto.http.trace_exporter", "OTLPSpanExporter", "http"),
        ("opentelemetry.exporter.otlp.proto.grpc.trace_exporter", "OTLPSpanExporter", "grpc"),
    ):
        fake = types.ModuleType(module)
        setattr(fake, name, lambda endpoint=None, label=label: made.append(label) or object())
        monkeypatch.setitem(sys.modules, module, fake)
    telemetry._otlp("traces", None)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    telemetry._otlp("traces", None)
    assert made == ["http", "grpc"]


def test_matrix_run_installs_the_sdk_and_keeps_stdout_for_its_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    clean: list[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv("MATRIX_CONFIG", raising=False)
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    (tmp_path / "matrix.yaml").write_text("matrix: {}\n")
    assert main(["run", "--json"]) == 0
    document: dict[str, Any] = json.loads(capsys.readouterr().out)  # stdout is only the run
    assert document["status"] == "completed" and clean == ["traces"]


def test_matrix_run_says_when_export_cannot_happen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv("MATRIX_CONFIG", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.setattr(telemetry, "_installed", lambda module=telemetry._SDK: False)
    (tmp_path / "matrix.yaml").write_text("matrix: {}\n")
    assert main(["run", "--json"]) == 0
    (warning,) = json.loads(capsys.readouterr().out)["warnings"]
    assert warning["kind"] == "extra_missing"
    assert matrix.EXIT_CODES["ok"] == 0
