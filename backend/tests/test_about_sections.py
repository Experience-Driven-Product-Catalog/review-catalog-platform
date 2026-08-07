from __future__ import annotations

import pytest
from fastapi import HTTPException

from review_catalog.api import routes
from review_catalog.settings import Settings


class _ReadmeResponse:
    text = "# remote README\n"

    def raise_for_status(self) -> None:
        return None


def test_about_sections_use_github_readmes_and_exact_local_profile(tmp_path, monkeypatch) -> None:
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _ReadmeResponse()

    monkeypatch.setattr(routes.httpx, "get", fake_get)
    profile = tmp_path / "resume.md"
    profile_markdown = "# 곽재원\n\n원문을 변경하지 않습니다.\n"
    profile.write_text(profile_markdown, encoding="utf-8")
    settings = Settings(
        catalog_data_root=tmp_path / "catalog",
        profile_markdown_path=profile,
        readme_request_timeout_seconds=2.5,
    )

    overview = routes.about_section("overview", settings)
    assert overview["markdown"] == "# remote README\n"
    assert overview["source_url"] == routes.REMOTE_README_URLS["overview"]
    assert calls == [
        (
            routes.REMOTE_README_URLS["overview"],
            {"follow_redirects": True, "timeout": 2.5},
        )
    ]

    about_me = routes.about_section("about-me", settings)
    assert about_me == {"markdown": profile_markdown, "source_url": None}

    with pytest.raises(HTTPException, match="about section not found") as exc_info:
        routes.about_section("unknown", settings)
    assert exc_info.value.status_code == 404
