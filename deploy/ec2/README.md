# 단일 EC2 배포 가이드

실제 AWS 리소스와 배포 절차는 [`../aws/README.md`](../aws/README.md)를 기준으로 한다. 이 문서는 현재 단일 EC2 topology와 별도 데이터 volume을 추가할 때의 확장 방향을 기록한다.

## 권장 topology

- Amazon Linux 2023 ARM64, 현재 데모 기준 2 vCPU / 8GB RAM
- catalog snapshot과 PostgreSQL volume을 위한 별도 암호화 gp3 EBS
- CloudFront 기본 도메인과 인증서로 viewer HTTPS를 종료하며 HTTP 요청은 HTTPS로 redirect한다.
- public subnet의 EC2 TCP 80은 AWS 관리형 CloudFront origin-facing prefix list에서만 허용하고, public IP 직접 접속은 차단한다.
- Airflow `8080`, FastAPI `8000`, PostgreSQL `5432` public ingress 금지
- SSH key 대신 SSM Session Manager 사용
- EC2 IAM Role에는 ECR pull, CloudWatch Logs, 필요한 Secrets Manager read만 최소 권한 부여

단일 호스트 장애 시 demo 전체가 중단되므로 production HA 구성은 아니다. 중요한 release는 주기적으로 S3에 object-lock 또는 versioning과 함께 백업한다.

## 설치

1. Docker Engine과 Compose v2를 설치한다.
2. repository를 `/opt/review-catalog-platform`에 배치한다.
3. 장기 운영으로 전환할 때 `/opt/review-catalog-data`에 별도 EBS를 mount하고 Docker named volume 또는 bind mount target으로 사용한다.
4. 현재는 EC2-local root-only `.env`를 사용한다. 장기 운영으로 전환할 때 Secrets Manager/SSM Parameter Store에서 materialize한다.
5. 다음을 실행한다.

```bash
cd /opt/review-catalog-platform
docker compose pull
docker compose build
docker compose up -d
docker compose ps
```

6. health check는 `/api/health`를 사용한다.
7. 최초 실제 Parquet migration을 FastAPI `POST /api/pipeline-runs`로 실행하고 749건 전체 이관을 확인한다.

## 배포 순서

schema/코드 호환성을 확인한 뒤 image를 build하고, `airflow-init`, `airflow-pool-init`, Airflow services, API, frontend 순으로 올라오게 한다. 새 container가 기존 immutable release를 읽을 수 있는지 먼저 확인한 후 ingestion을 허용한다.

## 백업

- PostgreSQL: `pg_dump`를 암호화 S3 prefix로 전송
- catalog: published `data/releases/`만 동기화; `work/`, `release-staging/`은 재생성 가능한 중간물
- 복구 시험에서 manifest의 SHA-256과 snapshot read-only open을 반드시 확인

## GitHub Actions와 CodeDeploy

`main` push 또는 수동 실행 시 GitHub Actions가 테스트와 secret scan을 통과한 tracked source만 private S3에 올리고 CodeDeploy가 EC2에서 immutable Git SHA release를 활성화한다. AWS 인증은 GitHub OIDC 단기 credential만 사용하며 장기 AWS access key, `.env`, OpenAI key를 저장소나 deployment artifact에 넣지 않는다. 정확한 사용법과 현재 리소스 식별자는 [`../aws/README.md`](../aws/README.md)를 따른다.
