"""Reusable, bounded-parallel Codex CLI structured-output runner."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Generic, TypeVar

from extraction.contracts import ReviewInput

OutputT = TypeVar("OutputT")
ResultParser = Callable[[ReviewInput, Mapping[str, object]], Sequence[OutputT]]
INPUT_MARKER = "### Input"


class CodexExtractor(Generic[OutputT]):
    """Run one Codex request per review and retain every successful raw result."""

    def __init__(
        self,
        *,
        task_name: str,
        prompt_path: Path,
        output_schema: Mapping[str, object],
        parse_result: ResultParser[OutputT],
        results_dir: Path,
        model: str,
        model_reasoning_effort: str,
        max_workers: int,
        timeout_seconds: int,
        progress_log_interval: int,
    ) -> None:
        if not task_name:
            raise ValueError("task_name은 비어 있을 수 없습니다.")
        for name, value in (
            ("model", model),
            ("model_reasoning_effort", model_reasoning_effort),
        ):
            if not isinstance(value, str) or not value.strip():
                raise TypeError(f"{name}은 비어 있지 않은 문자열이어야 합니다.")
        for name, value in (
            ("max_workers", max_workers),
            ("timeout_seconds", timeout_seconds),
            ("progress_log_interval", progress_log_interval),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise TypeError(f"{name}은 0보다 큰 정수여야 합니다.")

        self._task_name = task_name
        self._prompt_path = prompt_path
        self._output_schema = dict(output_schema)
        self._parse_result = parse_result
        self._results_dir = results_dir
        self._model = model
        self._model_reasoning_effort = model_reasoning_effort
        self._max_workers = max_workers
        self._timeout_seconds = timeout_seconds
        self._progress_log_interval = progress_log_interval
        self._static_prompt, self._input_template = self._load_prompt_parts()

    @property
    def max_workers(self) -> int:
        return self._max_workers

    def __call__(
        self,
        extraction_input: ReviewInput | Sequence[ReviewInput],
    ) -> list[OutputT]:
        if isinstance(extraction_input, ReviewInput):
            return self._extract_one(extraction_input)
        if not isinstance(extraction_input, Sequence) or isinstance(
            extraction_input, (str, bytes)
        ):
            raise TypeError(
                "extraction_input은 ReviewInput 또는 ReviewInput 시퀀스여야 합니다."
            )

        extraction_inputs = list(extraction_input)
        for index, item in enumerate(extraction_inputs):
            if not isinstance(item, ReviewInput):
                raise TypeError(
                    f"extraction_input[{index}]은 ReviewInput 객체여야 합니다."
                )
        if not extraction_inputs:
            return []
        return self._extract_many(extraction_inputs)

    def _extract_many(self, inputs: list[ReviewInput]) -> list[OutputT]:
        """Run bounded concurrent requests and flatten results in source order."""
        results: list[list[OutputT] | None] = [None] * len(inputs)
        failures: list[tuple[int, Exception]] = []
        completed_count = 0
        started_at = time.monotonic()
        worker_count = min(self._max_workers, len(inputs))
        logging.info(
            "%s extraction started: reviews=%s max_workers=%s",
            self._task_name,
            len(inputs),
            worker_count,
        )

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(self._extract_one, item): position
                for position, item in enumerate(inputs)
            }
            for future in as_completed(futures):
                position = futures[future]
                try:
                    results[position] = future.result()
                except Exception as exc:
                    failures.append((position, exc))

                completed_count += 1
                if (
                    completed_count == 1
                    or completed_count % self._progress_log_interval == 0
                    or completed_count == len(inputs)
                ):
                    logging.info(
                        "%s extraction progress: completed=%s/%s (%.1f%%)",
                        self._task_name,
                        completed_count,
                        len(inputs),
                        completed_count / len(inputs) * 100,
                    )

        if failures:
            position, exc = min(failures, key=lambda failure: failure[0])
            review_idx = inputs[position].review_idx
            raise RuntimeError(
                f"{self._task_name} 병렬 추출 중 review_idx={review_idx} 요청이 실패했습니다."
            ) from exc

        flattened = [
            item for batch in results if batch is not None for item in batch
        ]
        logging.info(
            "%s extraction completed: reviews=%s rows=%s elapsed_seconds=%.1f",
            self._task_name,
            len(inputs),
            len(flattened),
            time.monotonic() - started_at,
        )
        return flattened

    def _extract_one(self, review_input: ReviewInput) -> list[OutputT]:
        prompt = self._build_prompt(review_input)
        env = os.environ.copy()
        env.pop("OPENAI_API_KEY", None)
        env.pop("CODEX_API_KEY", None)

        with tempfile.TemporaryDirectory(
            prefix=f"codex-{self._task_name}-"
        ) as temporary_directory:
            workdir = Path(temporary_directory)
            schema_path = workdir / "output_schema.json"
            cli_result_path = workdir / "result.json"
            schema_path.write_text(
                json.dumps(self._output_schema, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            completed = self._run_cli(
                prompt=prompt,
                schema_path=schema_path,
                result_path=cli_result_path,
                workdir=workdir,
                env=env,
            )

            if completed.returncode != 0:
                cli_error = self._parse_cli_error(completed.stdout)
                error_details = cli_error or completed.stdout[-5000:] or "(없음)"
                raise RuntimeError(
                    "Codex CLI 실행에 실패했습니다.\n\n"
                    f"exit code: {completed.returncode}\n"
                    f"Codex error:\n{error_details}\n\n"
                    f"stderr:\n{completed.stderr[-5000:]}"
                )
            if not cli_result_path.exists():
                raise RuntimeError(
                    "Codex가 최종 결과 파일을 생성하지 않았습니다.\n\n"
                    f"stdout:\n{completed.stdout[-5000:]}\n\n"
                    f"stderr:\n{completed.stderr[-5000:]}"
                )

            response = self._read_json_object(cli_result_path)
            outputs = list(self._parse_result(review_input, response))
            self._save_result(review_input.review_idx, response)
            return outputs

    def _load_prompt_parts(self) -> tuple[str, str]:
        template = self._prompt_path.read_text(encoding="utf-8")
        static_part, marker, input_template = template.partition(INPUT_MARKER)
        if not marker:
            raise ValueError(
                f"프롬프트 파일에서 {INPUT_MARKER!r} 구간을 찾지 못했습니다."
            )

        for field_name in ("product_name", "product_category", "review"):
            placeholder = f"{{{{{field_name}}}}}"
            if placeholder not in input_template:
                raise ValueError(
                    f"프롬프트 입력 템플릿에 {placeholder!r} placeholder가 필요합니다."
                )
            input_template = input_template.replace(placeholder, f"{{{field_name}}}")
        return static_part.rstrip(), f"{INPUT_MARKER}{input_template}"

    def _build_prompt(self, review_input: ReviewInput) -> str:
        return "\n\n".join(
            (
                self._static_prompt,
                self._input_template.format(
                    product_name=review_input.product_name,
                    product_category=review_input.product_category,
                    review=review_input.review,
                ),
            )
        )

    def _run_cli(
        self,
        *,
        prompt: str,
        schema_path: Path,
        result_path: Path,
        workdir: Path,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "codex",
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
            self._model,
            "--config",
            f'model_reasoning_effort="{self._model_reasoning_effort}"',
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
            str(result_path),
            "-",
        ]
        try:
            return subprocess.run(
                command,
                input=prompt,
                text=True,
                encoding="utf-8",
                capture_output=True,
                cwd=workdir,
                env=env,
                timeout=self._timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "codex 실행 파일을 찾지 못했습니다. Codex CLI 설치와 PATH를 확인하세요."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"Codex 실행이 {self._timeout_seconds}초 안에 완료되지 않았습니다."
            ) from exc

    @staticmethod
    def _read_json_object(result_path: Path) -> dict[str, object]:
        raw_result = result_path.read_text(encoding="utf-8").strip()
        try:
            parsed = json.loads(raw_result)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Codex 최종 출력이 유효한 JSON이 아닙니다.\n\n{raw_result}"
            ) from exc
        if not isinstance(parsed, dict):
            raise TypeError("Codex 최종 출력은 JSON 객체여야 합니다.")
        return parsed

    def _save_result(
        self,
        review_idx: int,
        response: Mapping[str, object],
    ) -> None:
        """Atomically retain one validated raw result in the task-specific folder."""
        self._results_dir.mkdir(parents=True, exist_ok=True)
        destination = self._results_dir / f"{review_idx}.json"
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{review_idx}.", suffix=".tmp", dir=self._results_dir
        )
        os.close(handle)
        temporary_path = Path(temporary_name)
        try:
            temporary_path.write_text(
                json.dumps(response, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _parse_cli_error(jsonl_output: str) -> str | None:
        error_message: str | None = None
        for line in jsonl_output.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "error":
                message = event.get("message")
            elif event.get("type") == "turn.failed":
                error = event.get("error", {})
                message = error.get("message") if isinstance(error, dict) else None
            else:
                continue
            if isinstance(message, str) and message:
                error_message = message
        return error_message

