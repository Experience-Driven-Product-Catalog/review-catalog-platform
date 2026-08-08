from __future__ import annotations

from types import SimpleNamespace

from review_catalog.services.versions import _report_generator_identity


def test_report_generator_version_changes_with_report_source(tmp_path) -> None:
    reporting_root = tmp_path / "reporting"
    reporting_root.mkdir()
    source = reporting_root / "static.py"
    source.write_text("REPORT_FORMAT = 'v1'\n", encoding="utf-8")
    settings = SimpleNamespace(deployment_revision="local")

    first_id, first_version, first_sha = _report_generator_identity(settings, reporting_root)
    same_id, same_version, same_sha = _report_generator_identity(settings, reporting_root)

    assert (same_id, same_version, same_sha) == (first_id, first_version, first_sha)
    assert first_version == f"0.4.0+report.{first_sha[:12]}"

    source.write_text("REPORT_FORMAT = 'v2'\n", encoding="utf-8")
    second_id, second_version, second_sha = _report_generator_identity(settings, reporting_root)

    assert (second_id, second_version, second_sha) != (first_id, first_version, first_sha)
    assert second_version == f"0.4.0+report.{second_sha[:12]}"
