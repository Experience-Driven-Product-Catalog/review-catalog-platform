# 운영 Runbook

## release가 갱신되지 않을 때

1. `GET /api/pipeline-runs/{id}`에서 `state`, `current_task`, `error_message`를 확인한다.
2. `failed`이면 Airflow 해당 task log를 확인한다.
3. `pipeline_succeeded`가 오래 지속되면 FastAPI log에서 release finalizer 오류를 확인한다.
4. `release_failed`여도 이전 current release는 그대로 서비스된다. 원인을 고친 뒤 `review-catalog-finalize <run_id>`를 API container에서 실행한다.

## DuckDB write 충돌

정상 구성에서는 pool slot과 `fcntl` lock이 동시에 보호한다. `commit_catalog_delta`가 아닌 writer가 발견되면 즉시 중지하고 해당 process를 제거한다. FastAPI mount를 분리하더라도 connection은 계속 `read_only=True`를 유지한다.

## component version 교체

기존 row의 `version`이나 hash를 수정하지 않는다. 새 `ComponentVersion` row를 추가하고 transaction 안에서 이전 active를 false, 새 version을 true로 바꾼다. 다음 run의 `resolve_active_versions` 결과부터 적용된다. 과거 snapshot과 report는 변경하지 않는다.

수동 재군집화는 `Catalog_reclustering` DAG를 명시적으로 trigger한다. 시작 시 복사한 snapshot만 군집 입력으로 사용하고, commit 시점의 최신 snapshot 위에 새 taxonomy를 설치한다. 따라서 실행 중 추가된 Opinion Unit은 그대로 보존되지만 이전 mapping/embedding version을 유지하며 다음 재군집화에 포함된다. 새 component pointer는 release 디렉터리 publish와 같은 PostgreSQL transaction에서만 활성화된다.

## 초기 migration

`migration_manifests`에 `legacy-monitor-20260803-213339`이 없을 때만 전체 749건을 이관한다. 부분 migration은 허용하지 않는다. 복사된 source artifact의 SHA-256이 `config/taxonomy/20260803-213339.json`과 하나라도 다르면 commit 전에 실패한다. 이때 source Parquet을 수정해 맞추지 말고 새 migration/taxonomy version을 만든다.

## 새 리뷰 extraction 인증

기본 `codex_cli` provider는 Airflow image에 설치된 `@openai/codex`와 ChatGPT 인증을 사용한다. 최초 한 번 다음 명령을 실행한다.

```bash
docker compose exec airflow-scheduler codex login --device-auth
docker compose exec airflow-scheduler codex login status
```

`CODEX_HOME=/home/airflow/.codex`는 `codex_auth` named volume에 연결된다. 일반적인 `docker compose down`과 image 재빌드에는 인증이 유지되지만 `docker compose down -v`는 인증 volume도 삭제한다. `CODEX_CLI_VERSION`은 `.env`에서 고정하며 버전 변경 시 Airflow image를 다시 빌드한다.

대안으로 `EXTRACTION_BACKEND=openai`와 API key를 secret으로 제공할 수 있다. credential 부재를 `demo_rules` 같은 가짜 extraction으로 우회하지 않는다. 초기 migration과 보고서 조회는 외부 LLM 인증 없이 동작한다.

## 복구

현재 release가 손상되면 `catalog_releases.previous_release_id`가 가리키는 디렉터리의 manifest/hash를 검증하고 current pointer를 되돌린다. release 디렉터리 자체는 수정하지 않는다.
