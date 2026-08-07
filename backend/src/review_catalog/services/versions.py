from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from review_catalog import __version__
from review_catalog.db.models import ComponentVersion
from review_catalog.normalization.taxonomy import load_taxonomy_manifest
from review_catalog.settings import Settings


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _config_version(component: str, config: dict[str, str]) -> tuple[str, str, str]:
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    content_sha256 = hashlib.sha256(payload.encode()).hexdigest()
    version = ":".join(config.values())
    return f"{component}-{content_sha256[:16]}", version, content_sha256


def _report_generator_identity(settings: Settings) -> tuple[str, str, str]:
    reporting_root = Path(__file__).parents[1] / "reporting"
    digest = hashlib.sha256()
    for path in sorted(reporting_root.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    content_sha256 = digest.hexdigest()
    revision = settings.deployment_revision.strip()
    if revision and revision != "local":
        revision_label = revision[:12]
        version = f"{__version__}+git.{revision_label}"
    else:
        version = __version__
    identity_sha = hashlib.sha256(f"{content_sha256}:{version}".encode()).hexdigest()
    return f"report-generator-{identity_sha[:16]}", version, content_sha256


def bootstrap_component_versions(session: Session, settings: Settings) -> None:
    prompt_path = Path(__file__).parents[1] / "extraction/opinion_units_prompt.md"
    taxonomy = load_taxonomy_manifest(settings.taxonomy_manifest_path)
    extraction_model_name = settings.extraction_model
    extraction_id, extraction_version, extraction_sha = _config_version(
        "extraction-model",
        {
            "backend": settings.extraction_backend,
            "model": extraction_model_name,
            "reasoning_effort": settings.extraction_reasoning_effort,
        },
    )
    embedding_id = f"embedding-model-{taxonomy.embedding_model_artifact_sha256[:16]}"
    embedding_version = taxonomy.embedding_model_id
    embedding_sha = taxonomy.embedding_model_artifact_sha256
    report_id, report_version, report_sha = _report_generator_identity(settings)
    desired = (
        ComponentVersion(
            id=taxonomy.source_prompt_version_id,
            component_type="opinion_unit_prompt",
            version="extract-attribute-opinion-units-2026-08-03",
            content_sha256=_sha256(prompt_path),
            artifact_uri=str(prompt_path),
            metadata_json={
                "source": "copied-byte-for-byte-from-extract_attribute",
                "expected_sha256": taxonomy.source_prompt_sha256,
            },
            is_active=True,
        ),
        ComponentVersion(
            id=taxonomy.mapping_table_version_id,
            component_type="mapping_table",
            version=taxonomy.normalization_version,
            content_sha256=taxonomy.content_sha256,
            artifact_uri=str(settings.taxonomy_manifest_path),
            metadata_json={
                "normalization_run_id": taxonomy.normalization_run_id,
                "normalization_config_sha256": taxonomy.normalization_config_sha256,
                "canonicalization_policy": "historical-exact-map-else-complete-linkage-candidate",
            },
            is_active=True,
        ),
        ComponentVersion(
            id=extraction_id,
            component_type="extraction_model",
            version=extraction_version,
            content_sha256=extraction_sha,
            artifact_uri=None,
            metadata_json={
                "backend": settings.extraction_backend,
                "model": extraction_model_name,
                "reasoning_effort": settings.extraction_reasoning_effort,
            },
            is_active=True,
        ),
        ComponentVersion(
            id=embedding_id,
            component_type="embedding_model",
            version=embedding_version,
            content_sha256=embedding_sha,
            artifact_uri=(
                str(settings.embedding_model_path) if settings.embedding_model_path else None
            ),
            metadata_json={
                "backend": settings.embedding_backend,
                "model": settings.embedding_model_id,
                "artifact_sha256": taxonomy.embedding_model_artifact_sha256,
            },
            is_active=True,
        ),
        ComponentVersion(
            id=report_id,
            component_type="report_generator",
            version=report_version,
            content_sha256=report_sha,
            artifact_uri=None,
            metadata_json={
                "llm_used": False,
                "deployment_revision": settings.deployment_revision,
            },
            is_active=True,
        ),
    )
    if _sha256(prompt_path) != taxonomy.source_prompt_sha256:
        raise RuntimeError("copied Opinion Unit prompt differs from the source manifest")

    historical_extraction = session.get(
        ComponentVersion, taxonomy.source_extraction_model_version_id
    )
    if historical_extraction is None:
        session.add(
            ComponentVersion(
                id=taxonomy.source_extraction_model_version_id,
                component_type="historical_extraction_model",
                version=(
                    f"{taxonomy.source_extraction_backend}:"
                    f"{taxonomy.source_extraction_model}:"
                    f"{taxonomy.source_extraction_reasoning_effort}"
                ),
                content_sha256=hashlib.sha256(
                    json.dumps(
                        {
                            "backend": taxonomy.source_extraction_backend,
                            "model": taxonomy.source_extraction_model,
                            "reasoning_effort": taxonomy.source_extraction_reasoning_effort,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                artifact_uri=None,
                metadata_json={"source": "extract_attribute/config.yaml"},
                is_active=False,
            )
        )
    runtime_managed_types = {"mapping_table", "embedding_model"}
    active_ids = {
        component_type: component_id
        for component_type, component_id in session.execute(
            select(ComponentVersion.component_type, ComponentVersion.id).where(
                ComponentVersion.is_active.is_(True)
            )
        )
    }
    for version in desired:
        existing = session.get(ComponentVersion, version.id)
        if existing is None:
            preserve_runtime_active = (
                version.component_type in runtime_managed_types
                and version.component_type in active_ids
            )
            version.is_active = not preserve_runtime_active
            if version.is_active:
                session.execute(
                    update(ComponentVersion)
                    .where(
                        ComponentVersion.component_type == version.component_type,
                        ComponentVersion.is_active.is_(True),
                    )
                    .values(is_active=False)
                )
                active_ids[version.component_type] = version.id
            session.add(version)
            continue
        if (
            existing.component_type != version.component_type
            or existing.version != version.version
            or existing.content_sha256 != version.content_sha256
        ):
            raise RuntimeError(f"immutable component version content mismatch: {version.id}")
        preserve_runtime_active = (
            existing.component_type in runtime_managed_types
            and existing.component_type in active_ids
            and active_ids[existing.component_type] != existing.id
        )
        if not existing.is_active and not preserve_runtime_active:
            session.execute(
                update(ComponentVersion)
                .where(
                    ComponentVersion.component_type == version.component_type,
                    ComponentVersion.is_active.is_(True),
                )
                .values(is_active=False)
            )
            existing.is_active = True
            active_ids[existing.component_type] = existing.id
    # The caller owns the transaction. A flush makes newly bootstrapped rows
    # visible to later statements without closing an enclosing ``begin()``.
    session.flush()


def resolve_active_versions(session: Session) -> dict[str, dict[str, str | None]]:
    rows = session.scalars(
        select(ComponentVersion).where(ComponentVersion.is_active.is_(True))
    ).all()
    resolved = {
        row.component_type: {
            "id": row.id,
            "version": row.version,
            "content_sha256": row.content_sha256,
            "artifact_uri": row.artifact_uri,
        }
        for row in rows
    }
    required = {
        "opinion_unit_prompt",
        "extraction_model",
        "mapping_table",
        "embedding_model",
        "report_generator",
    }
    missing = required - set(resolved)
    if missing:
        raise RuntimeError(f"active component versions missing: {sorted(missing)}")
    return resolved
