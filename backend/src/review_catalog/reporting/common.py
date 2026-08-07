from __future__ import annotations

import json
import math
import os
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from review_catalog.pipeline.artifacts import canonical_sha256

SENTIMENTS = ("positive", "negative", "mixed", "neutral", "unknown")


def fetch_dicts(connection: duckdb.DuckDBPyConnection, query: str, params=None) -> list[dict]:
    cursor = connection.execute(query, params or [])
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def collapse_sentiments(values: set[str]) -> str:
    known = values - {"unknown"}
    if not known:
        return "unknown"
    if len(known) == 1:
        return next(iter(known))
    return "mixed"


def review_votes(rows: list[dict], fields: tuple[str, ...]) -> list[dict]:
    grouped: dict[tuple, set[str]] = defaultdict(set)
    for row in rows:
        if any(row.get(field) is None for field in fields):
            continue
        key = (row["review_id"], *[row.get(field) for field in fields])
        grouped[key].add(row["sentiment"])
    return [
        {
            "review_id": key[0],
            **{field: value for field, value in zip(fields, key[1:], strict=True)},
            "sentiment": collapse_sentiments(sentiments),
        }
        for key, sentiments in grouped.items()
    ]


def wilson_interval(
    successes: int, total: int, z: float = 1.959963984540054
) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = proportion + z * z / (2 * total)
    margin = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * total)) / total)
    return (max(0.0, (centre - margin) / denominator), min(1.0, (centre + margin) / denominator))


def sentiment_summary(votes: list[dict]) -> dict[str, Any]:
    counts = {sentiment: 0 for sentiment in SENTIMENTS}
    for vote in votes:
        counts[vote["sentiment"]] += 1
    total = len(votes)
    lower, upper = wilson_interval(counts["positive"], total)
    return {
        "counts": counts,
        "shares": {key: round(value / total, 6) if total else 0.0 for key, value in counts.items()},
        "positive_wilson_95": {"lower": round(lower, 6), "upper": round(upper, 6)},
    }


def product_rows(connection: duckdb.DuckDBPyConnection, product_id: str) -> list[dict]:
    return fetch_dicts(
        connection,
        """
        SELECT r.review_id, r.review_text, r.demo_review_id, r.product_id,
               o.* EXCLUDE (review_id)
        FROM reviews r
        JOIN opinion_units o USING (review_id)
        WHERE r.product_id = ?
        ORDER BY r.review_id, o.unit_position
        """,
        [product_id],
    )


def product_profile(connection: duckdb.DuckDBPyConnection, product_id: str) -> dict[str, float]:
    rows = [
        row
        for row in product_rows(connection, product_id)
        if row["mapping_state"] == "mapped_exact"
    ]
    votes = review_votes(rows, ("aspect_id", "aspect"))
    grouped: dict[str, list[dict]] = defaultdict(list)
    for vote in votes:
        grouped[vote["aspect_id"]].append(vote)
    return {
        aspect_id: round(
            sum(
                1
                if vote["sentiment"] == "positive"
                else -1
                if vote["sentiment"] == "negative"
                else 0
                for vote in aspect_votes
            )
            / len(aspect_votes),
            6,
        )
        for aspect_id, aspect_votes in grouped.items()
    }


def profile_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    common = sorted(set(left) & set(right))
    if not common:
        return 0.0
    distance = sum(abs(left[key] - right[key]) for key in common) / (2 * len(common))
    overlap = len(common) / max(len(set(left) | set(right)), 1)
    return round(0.7 * (1 - distance) + 0.3 * overlap, 6)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_report_pair(
    output_dir: Path, stem: str, payload: dict, markdown: str
) -> tuple[Path, Path]:
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    atomic_write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(markdown_path, markdown)
    return json_path, markdown_path


def report_id(prefix: str, identity: dict) -> str:
    return f"{prefix}:{canonical_sha256(identity)[:16]}"


def markdown_cell(value: object) -> str:
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def report_source(
    *, release_id: str, generated_at: datetime, versions: dict[str, Any]
) -> dict[str, Any]:
    """Expose catalog-release lineage in the source report's metadata shape."""
    mapping = versions["mapping_table"]
    embedding = versions["embedding_model"]
    prompt = versions["opinion_unit_prompt"]
    mapping_id = str(mapping["id"])
    mapping_sha = str(mapping.get("content_sha256") or "")
    return {
        "experiment": "D",
        "run_id": mapping_id.removeprefix("mapping-table-"),
        "created_at_local": generated_at.astimezone(ZoneInfo("Asia/Seoul")).isoformat(),
        "scope": "catalog_release_full_snapshot",
        "sampling_used": False,
        "run_manifest_sha256": mapping_sha,
        "normalization": {
            "embedding_model_id": embedding.get("version") or embedding["id"],
            "normalization_version": mapping.get("version") or mapping_id,
            "normalization_run_id": mapping_id.removeprefix("mapping-table-"),
            "normalization_config_sha256": mapping_sha,
            "mapping_distance_metric": "cosine",
        },
        "input_sha256": {
            "opinion_unit_prompt": prompt.get("content_sha256") or "",
            "mapping_table": mapping_sha,
        },
        "human_evaluation": {
            "status": "not_bundled_in_catalog_release",
            "completed_result_file_count": 0,
            "results_set_sha256": None,
            "evaluator_count": 0,
        },
        "catalog_release": {
            "release_id": release_id,
            "report_generator_version": versions["report_generator"].get("version")
            or versions["report_generator"]["id"],
        },
    }


def human_evaluation_placeholder() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "status": "not_bundled_in_catalog_release",
        "source": {
            "completed_result_file_count": 0,
            "results_set_sha256": None,
        },
        "validation": {"evaluator_count": 0, "minimum_evaluator_count": 3},
        "review_evaluation": None,
        "cluster_evaluation": None,
        "conclusion": "Catalog reports retain automatic lineage; human evaluation was not migrated.",
    }
