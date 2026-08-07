from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

from openai import OpenAI

from review_catalog.extraction.contracts import (
    OPINION_UNIT_JSON_SCHEMA,
    ExtractionResult,
    OpinionUnit,
    OpinionUnitResponse,
    ReviewInput,
)
from review_catalog.settings import Settings


class OpinionUnitExtractor(Protocol):
    def extract(self, reviews: Iterable[ReviewInput]) -> list[ExtractionResult]: ...


def _sha256_json(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _result(
    review: ReviewInput,
    units: list[OpinionUnit],
    *,
    prompt_version_id: str,
    model_version_id: str,
    raw_payload: object,
) -> ExtractionResult:
    return ExtractionResult(
        review=review,
        opinion_units=units,
        prompt_version_id=prompt_version_id,
        model_version_id=model_version_id,
        raw_response_sha256=_sha256_json(raw_payload),
    )


class OpenAIResponsesExtractor:
    def __init__(
        self,
        settings: Settings,
        prompt_path: Path,
        prompt_version_id: str,
        model_version_id: str,
    ) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("REVIEW_CATALOG_OPENAI_API_KEY is required for openai extraction")
        self.client = OpenAI(
            api_key=settings.openai_api_key, timeout=settings.extraction_timeout_seconds
        )
        self.model = settings.extraction_model
        self.reasoning_effort = settings.extraction_reasoning_effort
        self.static_prompt, self.input_template = _load_prompt_parts(prompt_path)
        self.prompt_version_id = prompt_version_id
        self.model_version_id = model_version_id
        self.max_workers = settings.extraction_max_workers

    def _extract_one(self, review: ReviewInput) -> ExtractionResult:
        response = self.client.responses.create(
            model=self.model,
            reasoning={"effort": self.reasoning_effort},
            input=[
                {"role": "developer", "content": self.static_prompt},
                {"role": "user", "content": _render_input(self.input_template, review)},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "opinion_units",
                    "schema": OPINION_UNIT_JSON_SCHEMA,
                    "strict": True,
                }
            },
        )
        payload = json.loads(response.output_text)
        parsed = OpinionUnitResponse.model_validate(payload)
        return _result(
            review,
            parsed.opinion_units,
            prompt_version_id=self.prompt_version_id,
            model_version_id=self.model_version_id,
            raw_payload=payload,
        )

    def extract(self, reviews: Iterable[ReviewInput]) -> list[ExtractionResult]:
        review_list = list(reviews)
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(review_list) or 1)) as pool:
            return list(pool.map(self._extract_one, review_list))


class CodexCLIExtractor:
    """Adapter matching the proven extract_attribute Codex CLI contract."""

    def __init__(
        self,
        settings: Settings,
        prompt_path: Path,
        prompt_version_id: str,
        model_version_id: str,
    ) -> None:
        self.executable = settings.codex_cli_executable
        self.model = settings.extraction_model
        self.reasoning_effort = settings.extraction_reasoning_effort
        self.static_prompt, self.input_template = _load_prompt_parts(prompt_path)
        self.prompt_version_id = prompt_version_id
        self.model_version_id = model_version_id
        self.timeout_seconds = settings.extraction_timeout_seconds
        self.max_workers = settings.extraction_max_workers

    def _extract_one(self, review: ReviewInput) -> ExtractionResult:
        complete_prompt = "\n\n".join(
            (self.static_prompt, _render_input(self.input_template, review))
        )
        with tempfile.TemporaryDirectory(prefix="review-catalog-codex-") as directory:
            root = Path(directory)
            schema_path = root / "schema.json"
            output_path = root / "output.json"
            schema_path.write_text(json.dumps(OPINION_UNIT_JSON_SCHEMA, ensure_ascii=False))
            command = [
                self.executable,
                "exec",
                "--json",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--color",
                "never",
                "--model",
                self.model,
                "--config",
                f'model_reasoning_effort="{self.reasoning_effort}"',
                "--config",
                'model_reasoning_summary="none"',
                "--config",
                'model_verbosity="low"',
                "--config",
                'web_search="disabled"',
                "--config",
                "agents.enabled=false",
                "--config",
                'forced_login_method="chatgpt"',
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-",
            ]
            env = dict(os.environ)
            env.pop("OPENAI_API_KEY", None)
            env.pop("CODEX_API_KEY", None)
            completed = subprocess.run(
                command,
                input=complete_prompt,
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            if completed.returncode != 0 or not output_path.exists():
                raise RuntimeError(f"Codex CLI extraction failed: {completed.stderr[-1000:]}")
            payload = json.loads(output_path.read_text())
        parsed = OpinionUnitResponse.model_validate(payload)
        return _result(
            review,
            parsed.opinion_units,
            prompt_version_id=self.prompt_version_id,
            model_version_id=self.model_version_id,
            raw_payload=payload,
        )

    def extract(self, reviews: Iterable[ReviewInput]) -> list[ExtractionResult]:
        review_list = list(reviews)
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(review_list) or 1)) as pool:
            return list(pool.map(self._extract_one, review_list))


def _load_prompt_parts(path: Path) -> tuple[str, str]:
    template = path.read_text(encoding="utf-8")
    static, marker, input_template = template.partition("### Input")
    if not marker:
        raise ValueError("Opinion Unit prompt is missing the ### Input section")
    return static.rstrip(), marker + input_template


def _render_input(template: str, review: ReviewInput) -> str:
    return (
        template.replace("{{product_name}}", review.product_name)
        .replace("{{product_category}}", review.product_category)
        .replace("{{review}}", review.review)
    )


def build_extractor(
    settings: Settings,
    *,
    prompt_version_id: str,
    model_version_id: str | None = None,
    prompt_path: Path | None = None,
) -> OpinionUnitExtractor:
    selected_path = prompt_path or Path(__file__).with_name("opinion_units_prompt.md")
    resolved_model_version_id = model_version_id or settings.extraction_model
    if settings.extraction_backend == "openai":
        return OpenAIResponsesExtractor(
            settings, selected_path, prompt_version_id, resolved_model_version_id
        )
    if settings.extraction_backend == "codex_cli":
        return CodexCLIExtractor(
            settings, selected_path, prompt_version_id, resolved_model_version_id
        )
    raise ValueError(f"unsupported extraction backend: {settings.extraction_backend}")
