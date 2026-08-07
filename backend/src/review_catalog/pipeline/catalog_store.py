from __future__ import annotations

import fcntl
import hashlib
import json
import shutil
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from review_catalog.normalization.taxonomy import TaxonomyManifest

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS products (
  product_id VARCHAR PRIMARY KEY,
  product_name VARCHAR NOT NULL,
  category VARCHAR NOT NULL,
  first_seen_run_id VARCHAR NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
  review_id VARCHAR PRIMARY KEY,
  source VARCHAR NOT NULL,
  source_review_id VARCHAR,
  external_review_idx BIGINT,
  product_id VARCHAR NOT NULL,
  review_text VARCHAR NOT NULL,
  content_sha256 VARCHAR NOT NULL,
  ingestion_run_id VARCHAR NOT NULL,
  demo_review_id VARCHAR,
  created_at TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_review_source_id ON reviews(source, source_review_id);
CREATE INDEX IF NOT EXISTS ix_review_content_sha ON reviews(content_sha256);
CREATE TABLE IF NOT EXISTS opinion_units (
  opinion_unit_id VARCHAR PRIMARY KEY,
  source_opinion_unit_idx BIGINT,
  review_id VARCHAR NOT NULL,
  unit_position INTEGER NOT NULL,
  raw_aspect VARCHAR NOT NULL,
  raw_status VARCHAR,
  excerpt VARCHAR NOT NULL,
  opinion VARCHAR NOT NULL,
  sentiment VARCHAR NOT NULL,
  mapping_state VARCHAR NOT NULL,
  aspect_id VARCHAR,
  aspect VARCHAR,
  status_id VARCHAR,
  status VARCHAR,
  suggested_aspect_id VARCHAR,
  suggested_aspect VARCHAR,
  aspect_distance DOUBLE,
  aspect_membership_max_distance DOUBLE,
  aspect_centroid_distance DOUBLE,
  aspect_second_nearest_distance DOUBLE,
  aspect_distance_margin DOUBLE,
  aspect_candidate_eligible BOOLEAN,
  suggested_status_id VARCHAR,
  suggested_status VARCHAR,
  status_distance DOUBLE,
  status_membership_max_distance DOUBLE,
  status_centroid_distance DOUBLE,
  status_second_nearest_distance DOUBLE,
  status_distance_margin DOUBLE,
  status_candidate_eligible BOOLEAN,
  prompt_version_id VARCHAR NOT NULL,
  model_version_id VARCHAR NOT NULL,
  mapping_table_version_id VARCHAR NOT NULL,
  embedding_model_version_id VARCHAR NOT NULL,
  normalization_run_id VARCHAR NOT NULL,
  normalization_config_sha256 VARCHAR NOT NULL,
  extraction_response_sha256 VARCHAR NOT NULL,
  ingestion_run_id VARCHAR NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  UNIQUE(review_id, unit_position, prompt_version_id, mapping_table_version_id)
);
CREATE INDEX IF NOT EXISTS ix_opinion_review ON opinion_units(review_id);
CREATE TABLE IF NOT EXISTS representative_attributes (
  representative_attribute_id VARCHAR PRIMARY KEY,
  source_attribute_idx BIGINT NOT NULL,
  review_id VARCHAR NOT NULL,
  raw_attribute VARCHAR NOT NULL,
  sentiment VARCHAR NOT NULL,
  source_artifact_sha256 VARCHAR NOT NULL,
  ingestion_run_id VARCHAR NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS taxonomy_versions (
  mapping_table_version_id VARCHAR PRIMARY KEY,
  normalization_version VARCHAR NOT NULL,
  normalization_run_id VARCHAR NOT NULL,
  normalization_config_sha256 VARCHAR NOT NULL,
  embedding_model_id VARCHAR NOT NULL,
  embedding_model_artifact_sha256 VARCHAR NOT NULL,
  metric VARCHAR NOT NULL,
  linkage VARCHAR NOT NULL,
  content_sha256 VARCHAR NOT NULL,
  ingested_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS taxonomy_rebuilds (
  recluster_run_id VARCHAR PRIMARY KEY,
  mapping_table_version_id VARCHAR NOT NULL,
  embedding_model_version_id VARCHAR NOT NULL,
  captured_snapshot_sha256 VARCHAR NOT NULL,
  captured_opinion_unit_count BIGINT NOT NULL,
  carried_forward_opinion_unit_count BIGINT NOT NULL,
  rebuilt_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS aspect_clusters (
  mapping_table_version_id VARCHAR NOT NULL,
  aspect_cluster_id VARCHAR NOT NULL,
  canonical_label VARCHAR NOT NULL,
  medoid_label VARCHAR NOT NULL,
  naming_status VARCHAR NOT NULL,
  member_count BIGINT NOT NULL,
  source_row_count BIGINT NOT NULL,
  unique_review_count BIGINT NOT NULL,
  distance_threshold DOUBLE NOT NULL,
  naming_max_distance DOUBLE NOT NULL,
  cluster_max_distance DOUBLE NOT NULL,
  representative_average_distance DOUBLE,
  representative_max_distance DOUBLE,
  representative_centroid_distance DOUBLE,
  member_expressions VARCHAR[] NOT NULL,
  member_details_json VARCHAR NOT NULL,
  centroid_embedding DOUBLE[] NOT NULL,
  canonical_embedding DOUBLE[] NOT NULL,
  PRIMARY KEY(mapping_table_version_id, aspect_cluster_id)
);
CREATE TABLE IF NOT EXISTS aspect_mapping_table (
  mapping_table_version_id VARCHAR NOT NULL,
  raw_aspect VARCHAR NOT NULL,
  aspect_cluster_id VARCHAR NOT NULL,
  canonical_label VARCHAR NOT NULL,
  naming_status VARCHAR NOT NULL,
  mapping_applied BOOLEAN NOT NULL,
  mapping_distance DOUBLE NOT NULL,
  source_row_count BIGINT NOT NULL,
  unique_review_count BIGINT NOT NULL,
  embedding DOUBLE[] NOT NULL,
  PRIMARY KEY(mapping_table_version_id, raw_aspect)
);
CREATE TABLE IF NOT EXISTS status_clusters (
  mapping_table_version_id VARCHAR NOT NULL,
  aspect_cluster_id VARCHAR NOT NULL,
  status_cluster_id VARCHAR NOT NULL,
  canonical_label VARCHAR NOT NULL,
  medoid_label VARCHAR NOT NULL,
  naming_status VARCHAR NOT NULL,
  member_count BIGINT NOT NULL,
  source_row_count BIGINT NOT NULL,
  unique_review_count BIGINT NOT NULL,
  distance_threshold DOUBLE NOT NULL,
  naming_max_distance DOUBLE NOT NULL,
  cluster_max_distance DOUBLE NOT NULL,
  representative_average_distance DOUBLE,
  representative_max_distance DOUBLE,
  representative_centroid_distance DOUBLE,
  cannot_link_pair_count_in_boundary BIGINT NOT NULL,
  member_expressions VARCHAR[] NOT NULL,
  member_details_json VARCHAR NOT NULL,
  centroid_embedding DOUBLE[] NOT NULL,
  canonical_embedding DOUBLE[] NOT NULL,
  PRIMARY KEY(mapping_table_version_id, status_cluster_id)
);
CREATE TABLE IF NOT EXISTS status_mapping_table (
  mapping_table_version_id VARCHAR NOT NULL,
  aspect_cluster_id VARCHAR NOT NULL,
  raw_status VARCHAR NOT NULL,
  status_cluster_id VARCHAR NOT NULL,
  canonical_label VARCHAR NOT NULL,
  naming_status VARCHAR NOT NULL,
  mapping_applied BOOLEAN NOT NULL,
  mapping_distance DOUBLE NOT NULL,
  source_row_count BIGINT NOT NULL,
  unique_review_count BIGINT NOT NULL,
  embedding DOUBLE[] NOT NULL,
  PRIMARY KEY(mapping_table_version_id, aspect_cluster_id, raw_status)
);
CREATE TABLE IF NOT EXISTS source_artifacts (
  sha256 VARCHAR PRIMARY KEY,
  artifact_name VARCHAR NOT NULL,
  source_project VARCHAR NOT NULL,
  source_path VARCHAR NOT NULL,
  row_count BIGINT,
  imported_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS migration_manifests (
  migration_id VARCHAR PRIMARY KEY,
  source_normalization_run_id VARCHAR NOT NULL,
  source_manifest_sha256 VARCHAR NOT NULL,
  source_review_count BIGINT NOT NULL,
  source_opinion_unit_count BIGINT NOT NULL,
  source_representative_attribute_count BIGINT NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog_commits (
  run_id VARCHAR PRIMARY KEY,
  committed_at TIMESTAMPTZ NOT NULL,
  review_count INTEGER NOT NULL,
  opinion_unit_count INTEGER NOT NULL,
  candidate_count INTEGER NOT NULL,
  versions_json JSON NOT NULL,
  delta_sha256 VARCHAR NOT NULL
);
"""


@contextmanager
def readonly_catalog(snapshot_path: Path):
    connection = duckdb.connect(str(snapshot_path), read_only=True)
    try:
        yield connection
    finally:
        connection.close()


def existing_review_hashes(snapshot_path: Path | None) -> set[str]:
    if snapshot_path is None or not snapshot_path.exists():
        return set()
    with readonly_catalog(snapshot_path) as connection:
        return {
            row[0] for row in connection.execute("SELECT content_sha256 FROM reviews").fetchall()
        }


def catalog_has_legacy_migration(snapshot_path: Path | None) -> bool:
    if snapshot_path is None or not snapshot_path.exists():
        return False
    try:
        with readonly_catalog(snapshot_path) as connection:
            return bool(
                connection.execute(
                    """
                    SELECT count(*) FROM information_schema.tables
                    WHERE table_name = 'migration_manifests'
                    """
                ).fetchone()[0]
                and connection.execute(
                    "SELECT count(*) FROM migration_manifests WHERE migration_id = ?",
                    ["legacy-monitor-20260803-213339"],
                ).fetchone()[0]
            )
    except duckdb.Error:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_artifacts(root: Path, manifest: TaxonomyManifest) -> dict[str, Path]:
    paths = {
        "monitor_reviews.parquet": root / "legacy_monitor/monitor_reviews.parquet",
        "monitor_opinion_units.parquet": root / "legacy_monitor/monitor_opinion_units.parquet",
        "monitor_representative_attributes.parquet": root
        / "legacy_monitor/monitor_representative_attributes.parquet",
        "experiment_d.parquet": root / "taxonomy/20260803-213339/experiment_d.parquet",
        "experiment_d_aspect_nodes.parquet": root
        / "taxonomy/20260803-213339/experiment_d_aspect_nodes.parquet",
        "experiment_d_aspect_clusters.parquet": root
        / "taxonomy/20260803-213339/experiment_d_aspect_clusters.parquet",
        "experiment_d_status_nodes.parquet": root
        / "taxonomy/20260803-213339/experiment_d_status_nodes.parquet",
        "experiment_d_status_clusters.parquet": root
        / "taxonomy/20260803-213339/experiment_d_status_clusters.parquet",
    }
    for name, expected_hash in manifest.artifacts.items():
        path = paths[name]
        if not path.is_file():
            raise FileNotFoundError(f"migration artifact is missing: {path}")
        actual_hash = _sha256(path)
        if actual_hash != expected_hash:
            raise ValueError(
                f"migration artifact hash mismatch for {name}: {actual_hash} != {expected_hash}"
            )
    return paths


def _install_mapping_bundle(
    connection: duckdb.DuckDBPyConnection,
    *,
    paths: dict[str, Path],
    manifest: TaxonomyManifest,
    now: datetime,
) -> None:
    version_id = manifest.mapping_table_version_id
    connection.execute(
        "INSERT INTO taxonomy_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            version_id,
            manifest.normalization_version,
            manifest.normalization_run_id,
            manifest.normalization_config_sha256,
            manifest.embedding_model_id,
            manifest.embedding_model_artifact_sha256,
            manifest.metric,
            manifest.linkage,
            manifest.content_sha256,
            now,
        ],
    )
    connection.execute(
        """
        INSERT INTO aspect_clusters
        SELECT ?, cluster_id, canonical_label, medoid_label, naming_status,
               member_count, source_row_count, unique_review_count,
               distance_threshold, naming_max_distance, cluster_max_distance,
               representative_average_distance, representative_max_distance,
               representative_centroid_distance, member_expressions,
               member_details_json, centroid_embedding, canonical_embedding
        FROM read_parquet(?)
        """,
        [version_id, str(paths["experiment_d_aspect_clusters.parquet"])],
    )
    connection.execute(
        """
        INSERT INTO aspect_mapping_table
        SELECT ?, raw_aspect, cluster_id, canonical_label, naming_status,
               mapping_applied, mapping_distance, source_row_count,
               unique_review_count, embedding
        FROM read_parquet(?)
        """,
        [version_id, str(paths["experiment_d_aspect_nodes.parquet"])],
    )
    connection.execute(
        """
        INSERT INTO status_clusters
        SELECT ?, aspect_cluster_id, cluster_id, canonical_label, medoid_label,
               naming_status, member_count, source_row_count, unique_review_count,
               distance_threshold, naming_max_distance, cluster_max_distance,
               representative_average_distance, representative_max_distance,
               representative_centroid_distance,
               status_cannot_link_pair_count_in_boundary, member_expressions,
               member_details_json, centroid_embedding, canonical_embedding
        FROM read_parquet(?)
        """,
        [version_id, str(paths["experiment_d_status_clusters.parquet"])],
    )
    connection.execute(
        """
        INSERT INTO status_mapping_table
        SELECT ?, aspect_cluster_id, raw_status, cluster_id, canonical_label,
               naming_status, mapping_applied, mapping_distance,
               source_row_count, unique_review_count, embedding
        FROM read_parquet(?)
        """,
        [version_id, str(paths["experiment_d_status_nodes.parquet"])],
    )


def _verified_recluster_artifacts(root: Path, manifest: TaxonomyManifest) -> dict[str, Path]:
    required = {
        "experiment_d.parquet",
        "experiment_d_aspect_nodes.parquet",
        "experiment_d_aspect_clusters.parquet",
        "experiment_d_status_nodes.parquet",
        "experiment_d_status_clusters.parquet",
        "embedding_model_manifest.json",
    }
    if set(manifest.artifacts) != required:
        raise ValueError("reclustering artifact inventory differs from the required bundle")
    paths = {name: root / name for name in required}
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"reclustering artifact is missing: {path}")
        actual_hash = _sha256(path)
        if actual_hash != manifest.artifacts[name]:
            raise ValueError(
                f"reclustering artifact hash mismatch for {name}: "
                f"{actual_hash} != {manifest.artifacts[name]}"
            )
    return paths


def _import_legacy_catalog(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    versions: dict,
    root: Path,
    manifest: TaxonomyManifest,
    now: datetime,
) -> dict[str, int]:
    paths = _verified_artifacts(root, manifest)
    _install_mapping_bundle(connection, paths=paths, manifest=manifest, now=now)
    reviews_path = str(paths["monitor_reviews.parquet"])
    opinion_path = str(paths["monitor_opinion_units.parquet"])
    representative_path = str(paths["monitor_representative_attributes.parquet"])
    experiment_d_path = str(paths["experiment_d.parquet"])
    connection.execute(
        """
        INSERT INTO products
        SELECT 'monitor-' || substr(sha256(productName), 1, 16), productName,
               '모니터', ?, ?
        FROM read_parquet(?) GROUP BY productName ORDER BY productName
        """,
        [run_id, now, reviews_path],
    )
    connection.execute(
        """
        INSERT INTO reviews
        SELECT 'legacy-review-' || idx::VARCHAR, 'legacy_parquet', idx::VARCHAR,
               idx, 'monitor-' || substr(sha256(productName), 1, 16), review,
               sha256(trim(review)), ?, NULL, ?
        FROM read_parquet(?) ORDER BY idx
        """,
        [run_id, now, reviews_path],
    )
    cache_root = root / "legacy_monitor/codex_results/opinion_units"
    response_hashes = []
    for row in connection.execute(
        "SELECT idx FROM read_parquet(?) ORDER BY idx", [reviews_path]
    ).fetchall():
        review_idx = int(row[0])
        cache_path = cache_root / f"{review_idx}.json"
        if not cache_path.is_file():
            raise FileNotFoundError(f"legacy Codex response is missing: {cache_path}")
        response_hashes.append((review_idx, _sha256(cache_path)))
    connection.execute(
        "CREATE TEMP TABLE legacy_response_hashes(review_idx BIGINT, response_sha256 VARCHAR)"
    )
    connection.executemany("INSERT INTO legacy_response_hashes VALUES (?, ?)", response_hashes)
    connection.execute(
        """
        INSERT INTO opinion_units
        SELECT
          'legacy-ou-' || o.idx::VARCHAR, o.idx, 'legacy-review-' || o.review_idx::VARCHAR,
          row_number() OVER (PARTITION BY o.review_idx ORDER BY o.idx)::INTEGER,
          o.raw_aspect, o.raw_status, o.excerpt, o.opinion, o.sentiment,
          CASE WHEN d.idx IS NULL THEN 'excluded_taxonomy' ELSE 'mapped_exact' END,
          d.aspect_cluster_id, d.aspect, d.status_cluster_id, d.status,
          NULL, NULL, d.aspect_mapping_distance,
          NULL, NULL, NULL, NULL, NULL,
          NULL, NULL, d.status_mapping_distance,
          NULL, NULL, NULL, NULL, NULL,
          ?, ?, ?, ?, ?, ?, h.response_sha256, ?, ?
        FROM read_parquet(?) o
        LEFT JOIN read_parquet(?) d USING (idx)
        JOIN legacy_response_hashes h ON h.review_idx = o.review_idx
        ORDER BY o.idx
        """,
        [
            manifest.source_prompt_version_id,
            manifest.source_extraction_model_version_id,
            versions["mapping_table"]["id"],
            versions["embedding_model"]["id"],
            manifest.normalization_run_id,
            manifest.normalization_config_sha256,
            run_id,
            now,
            opinion_path,
            experiment_d_path,
        ],
    )
    representative_hash = manifest.artifacts["monitor_representative_attributes.parquet"]
    connection.execute(
        """
        INSERT INTO representative_attributes
        SELECT 'legacy-ra-' || idx::VARCHAR, idx,
               'legacy-review-' || review_idx::VARCHAR,
               raw_attribute, sentiment, ?, ?, ?
        FROM read_parquet(?) ORDER BY idx
        """,
        [representative_hash, run_id, now, representative_path],
    )
    row_counts = {
        "monitor_reviews.parquet": 749,
        "monitor_opinion_units.parquet": 2583,
        "monitor_representative_attributes.parquet": 2352,
        "experiment_d.parquet": 2401,
        "experiment_d_aspect_nodes.parquet": 870,
        "experiment_d_aspect_clusters.parquet": 408,
        "experiment_d_status_nodes.parquet": 1457,
        "experiment_d_status_clusters.parquet": 1320,
    }
    for name, path in paths.items():
        connection.execute(
            "INSERT INTO source_artifacts VALUES (?, ?, ?, ?, ?, ?)",
            [
                manifest.artifacts[name],
                name,
                "extract_attribute"
                if name.startswith("monitor_")
                else "embedding_clustering_experiment",
                str(path),
                row_counts[name],
                now,
            ],
        )
    connection.execute(
        "INSERT INTO migration_manifests VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            "legacy-monitor-20260803-213339",
            manifest.normalization_run_id,
            manifest.content_sha256,
            749,
            2583,
            2352,
            now,
        ],
    )
    return {"review_count": 749, "opinion_unit_count": 2583, "candidate_count": 0}


def _append_delta(
    connection: duckdb.DuckDBPyConnection,
    *,
    delta: dict,
    manifest: TaxonomyManifest,
    now: datetime,
) -> dict[str, int]:
    for product in delta["products"]:
        connection.execute(
            "INSERT OR IGNORE INTO products VALUES (?, ?, ?, ?, ?)",
            [
                product["product_id"],
                product["product_name"],
                product["category"],
                delta["run_id"],
                now,
            ],
        )
    review_columns = (
        "review_id",
        "source",
        "source_review_id",
        "external_review_idx",
        "product_id",
        "review_text",
        "content_sha256",
        "ingestion_run_id",
        "demo_review_id",
        "created_at",
    )
    for review in delta["reviews"]:
        connection.execute(
            f"INSERT INTO reviews ({', '.join(review_columns)}) "
            f"VALUES ({', '.join(['?'] * len(review_columns))})",
            [review.get(column) if column != "created_at" else now for column in review_columns],
        )
    unit_columns = (
        "opinion_unit_id",
        "source_opinion_unit_idx",
        "review_id",
        "unit_position",
        "raw_aspect",
        "raw_status",
        "excerpt",
        "opinion",
        "sentiment",
        "mapping_state",
        "aspect_id",
        "aspect",
        "status_id",
        "status",
        "suggested_aspect_id",
        "suggested_aspect",
        "aspect_distance",
        "aspect_membership_max_distance",
        "aspect_centroid_distance",
        "aspect_second_nearest_distance",
        "aspect_distance_margin",
        "aspect_candidate_eligible",
        "suggested_status_id",
        "suggested_status",
        "status_distance",
        "status_membership_max_distance",
        "status_centroid_distance",
        "status_second_nearest_distance",
        "status_distance_margin",
        "status_candidate_eligible",
        "prompt_version_id",
        "model_version_id",
        "mapping_table_version_id",
        "embedding_model_version_id",
        "normalization_run_id",
        "normalization_config_sha256",
        "extraction_response_sha256",
        "ingestion_run_id",
        "created_at",
    )
    for unit in delta["opinion_units"]:
        connection.execute(
            f"INSERT INTO opinion_units ({', '.join(unit_columns)}) "
            f"VALUES ({', '.join(['?'] * len(unit_columns))})",
            [unit.get(column) if column != "created_at" else now for column in unit_columns],
        )
    return {
        "review_count": len(delta["reviews"]),
        "opinion_unit_count": len(delta["opinion_units"]),
        "candidate_count": sum(
            unit["mapping_state"] == "candidate" for unit in delta["opinion_units"]
        ),
    }


def commit_catalog_delta(
    *,
    destination: Path,
    previous_snapshot: Path | None,
    delta: dict,
    taxonomy_manifest: TaxonomyManifest,
    legacy_migration_root: Path,
    writer_lock_path: Path,
    writer_identity: str,
) -> dict[str, int]:
    """The sole DuckDB write entrypoint; Airflow must own the writer identity."""
    if writer_identity != "airflow":
        raise PermissionError("DuckDB writes are restricted to Airflow tasks")
    destination.parent.mkdir(parents=True, exist_ok=True)
    writer_lock_path.parent.mkdir(parents=True, exist_ok=True)
    with writer_lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if previous_snapshot and previous_snapshot.exists():
            shutil.copy2(previous_snapshot, destination)
        else:
            destination.unlink(missing_ok=True)
        connection = duckdb.connect(str(destination))
        transaction_started = False
        try:
            connection.execute(SCHEMA_SQL)
            connection.execute("BEGIN TRANSACTION")
            transaction_started = True
            now = datetime.now(UTC)
            if delta.get("legacy_migration"):
                counts = _import_legacy_catalog(
                    connection,
                    run_id=delta["run_id"],
                    versions=delta["versions"],
                    root=legacy_migration_root,
                    manifest=taxonomy_manifest,
                    now=now,
                )
            else:
                counts = _append_delta(connection, delta=delta, manifest=taxonomy_manifest, now=now)
            connection.execute(
                "INSERT INTO catalog_commits VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    delta["run_id"],
                    now,
                    counts["review_count"],
                    counts["opinion_unit_count"],
                    counts["candidate_count"],
                    json.dumps(delta["versions"], ensure_ascii=False, sort_keys=True),
                    delta["delta_sha256"],
                ],
            )
            connection.execute("COMMIT")
            transaction_started = False
            connection.execute("CHECKPOINT")
        except Exception:
            if transaction_started:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return counts


def commit_taxonomy_rebuild(
    *,
    destination: Path,
    previous_snapshot: Path,
    artifact_root: Path,
    taxonomy_manifest: TaxonomyManifest,
    embedding_model_version_id: str,
    recluster_run_id: str,
    captured_snapshot_sha256: str,
    writer_lock_path: Path,
    writer_identity: str,
) -> dict[str, int]:
    """Install one full taxonomy and remap only rows captured at DAG start."""
    if writer_identity != "airflow":
        raise PermissionError("DuckDB writes are restricted to Airflow tasks")
    if not previous_snapshot.is_file():
        raise FileNotFoundError(f"base catalog snapshot is missing: {previous_snapshot}")
    paths = _verified_recluster_artifacts(artifact_root, taxonomy_manifest)
    destination.parent.mkdir(parents=True, exist_ok=True)
    writer_lock_path.parent.mkdir(parents=True, exist_ok=True)
    with writer_lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        shutil.copy2(previous_snapshot, destination)
        connection = duckdb.connect(str(destination))
        transaction_started = False
        try:
            connection.execute(SCHEMA_SQL)
            connection.execute("BEGIN TRANSACTION")
            transaction_started = True
            now = datetime.now(UTC)
            if connection.execute(
                "SELECT count(*) FROM taxonomy_versions WHERE mapping_table_version_id = ?",
                [taxonomy_manifest.mapping_table_version_id],
            ).fetchone()[0]:
                raise ValueError(
                    "mapping table version already exists in the base snapshot: "
                    f"{taxonomy_manifest.mapping_table_version_id}"
                )
            _install_mapping_bundle(
                connection,
                paths=paths,
                manifest=taxonomy_manifest,
                now=now,
            )
            connection.execute(
                "CREATE TEMP TABLE recluster_assignments AS SELECT * FROM read_parquet(?)",
                [str(paths["experiment_d.parquet"])],
            )
            captured_count = int(
                connection.execute("SELECT count(*) FROM recluster_assignments").fetchone()[0]
            )
            missing_count = int(
                connection.execute(
                    """
                    SELECT count(*)
                    FROM recluster_assignments a
                    LEFT JOIN opinion_units o USING (opinion_unit_id)
                    WHERE o.opinion_unit_id IS NULL
                    """
                ).fetchone()[0]
            )
            if missing_count:
                raise ValueError(f"base snapshot is missing {missing_count} captured opinion units")
            connection.execute(
                """
                UPDATE opinion_units AS o SET
                  mapping_state = a.mapping_state,
                  aspect_id = a.aspect_cluster_id,
                  aspect = a.aspect,
                  status_id = a.status_cluster_id,
                  status = a.status,
                  suggested_aspect_id = NULL,
                  suggested_aspect = NULL,
                  aspect_distance = a.aspect_mapping_distance,
                  aspect_membership_max_distance = NULL,
                  aspect_centroid_distance = NULL,
                  aspect_second_nearest_distance = NULL,
                  aspect_distance_margin = NULL,
                  aspect_candidate_eligible = NULL,
                  suggested_status_id = NULL,
                  suggested_status = NULL,
                  status_distance = a.status_mapping_distance,
                  status_membership_max_distance = NULL,
                  status_centroid_distance = NULL,
                  status_second_nearest_distance = NULL,
                  status_distance_margin = NULL,
                  status_candidate_eligible = NULL,
                  mapping_table_version_id = ?,
                  embedding_model_version_id = ?,
                  normalization_run_id = ?,
                  normalization_config_sha256 = ?
                FROM recluster_assignments AS a
                WHERE o.opinion_unit_id = a.opinion_unit_id
                """,
                [
                    taxonomy_manifest.mapping_table_version_id,
                    embedding_model_version_id,
                    taxonomy_manifest.normalization_run_id,
                    taxonomy_manifest.normalization_config_sha256,
                ],
            )
            total_count = int(
                connection.execute("SELECT count(*) FROM opinion_units").fetchone()[0]
            )
            carried_forward = total_count - captured_count
            connection.execute(
                "INSERT INTO taxonomy_rebuilds VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    recluster_run_id,
                    taxonomy_manifest.mapping_table_version_id,
                    embedding_model_version_id,
                    captured_snapshot_sha256,
                    captured_count,
                    carried_forward,
                    now,
                ],
            )
            connection.execute("COMMIT")
            transaction_started = False
            connection.execute("CHECKPOINT")
        except Exception:
            if transaction_started:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return {
        "captured_opinion_unit_count": captured_count,
        "carried_forward_opinion_unit_count": carried_forward,
        "aspect_mapping_expression_count": taxonomy_manifest.counts["aspect_mapping_expressions"],
        "aspect_cluster_count": taxonomy_manifest.counts["aspect_clusters"],
        "status_mapping_expression_count": taxonomy_manifest.counts[
            "aspect_status_mapping_expressions"
        ],
        "status_cluster_count": taxonomy_manifest.counts["status_clusters"],
    }
