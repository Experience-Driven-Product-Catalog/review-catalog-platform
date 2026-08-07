from __future__ import annotations

from types import SimpleNamespace

from review_catalog.pipeline import stages
from review_catalog.pipeline.artifacts import atomic_write_json, read_json


def test_demo_reviews_are_events_and_are_not_content_deduplicated(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(stages, "_set_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(stages, "_current_snapshot", lambda: None)
    monkeypatch.setattr(stages, "existing_review_hashes", lambda _: {"same-content"})
    monkeypatch.setattr(
        stages,
        "get_settings",
        lambda: SimpleNamespace(work_root=tmp_path),
    )
    staged_path = tmp_path / "staged.json"
    atomic_write_json(
        staged_path,
        {
            "mode": "incremental",
            "reviews": [
                {
                    "source": "seed",
                    "content_sha256": "same-content",
                    "demo_review_id": None,
                },
                {
                    "source": "demo_ui",
                    "content_sha256": "same-content",
                    "demo_review_id": "demo-1",
                },
                {
                    "source": "demo_ui",
                    "content_sha256": "same-content",
                    "demo_review_id": "demo-2",
                },
            ],
        },
    )

    result_path = stages.deduplicate_reviews("run-test", str(staged_path))
    result = read_json(result_path)

    assert [row["demo_review_id"] for row in result["reviews"]] == ["demo-1", "demo-2"]
    assert result["duplicate_count"] == 1
