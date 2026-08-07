# 데이터 모델과 불변조건

## PostgreSQL

- `component_versions`: prompt, extraction model/provider, mapping table, embedding model, report generator의 immutable version registry
- `pipeline_runs`: trigger, task progress, resolved versions, candidate count, release 상태
- `demo_submissions`, `demo_reviews`: UI가 제출한 review만 식별하는 운영 record
- `catalog_releases`: published snapshot과 manifest pointer, 현재 release pointer
- `report_artifacts`: release에 포함된 Markdown의 target과 SHA-256

## DuckDB

- `products`: 상품 identity
- `reviews`: 원문, source, content hash, ingestion run, UI review lineage
- `opinion_units`: raw extraction, exact canonical mapping, complete-linkage candidate 거리 진단, 모든 component version
- `representative_attributes`: 기존 direct-attribute 결과를 분리 보존
- `taxonomy_versions`: normalization run/config/model identity
- `aspect_mapping_table`, `status_mapping_table`: 기존 raw 표현의 exact lookup table과 node embedding
- `aspect_clusters`, `status_clusters`: canonical/medoid, threshold, member, centroid/canonical embedding
- `source_artifacts`, `migration_manifests`: 원본 Parquet hash와 최초 이관 receipt
- `catalog_commits`: run별 append commit receipt

## 강제 불변조건

1. `commit_catalog_delta(..., writer_identity="airflow")` 이외의 호출자는 거부한다.
2. Airflow task에는 `duckdb_writer_pool`, `pool_slots=1`을 지정한다.
3. API와 report generator의 모든 DuckDB connection은 `read_only=True`다.
4. 미등록 raw 표현의 canonical label은 null이고 complete-linkage suggestion만 기록한다.
5. status mapping과 추론은 `aspect_cluster_id` boundary 밖으로 나가지 않는다.
6. 초기 Parquet은 immutable migration input이며 이후 runtime output은 DuckDB에만 기록한다.
7. release는 snapshot과 report hash가 모두 manifest에 들어간 뒤 directory rename 한 번으로 공개한다.
8. 동적 제안서는 `demo_review_id`가 있는 UI 입력에 대해서만 생성한다.
