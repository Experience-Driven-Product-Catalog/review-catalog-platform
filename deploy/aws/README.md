# AWS 단일 EC2 배포

## 현재 배포

- 공개 URL: `https://d1my2fe0yb8h33.cloudfront.net`
- public GitHub repository: `https://github.com/Experience-Driven-Product-Catalog/review-catalog-platform`
- CloudFront distribution: `E22S4ED9XEUTWO`
- CodeDeploy application/group: `review-catalog-platform` / `review-catalog-platform-production`
- region/AZ: `ap-northeast-2` / `ap-northeast-2a`
- EC2: `i-0ada1de1769b3214d`, `t4g.large`, Amazon Linux 2023 ARM64
- EBS: encrypted gp3 40 GiB
- Elastic IP: `43.202.138.66`
- VPC/subnet: `vpc-02f3b928915885bef` / `subnet-066201c63d89f2f12`
- security group: `sg-0bc03838e9882de36`; TCP 80은 CloudFront origin-facing prefix list `pl-22a6434b`만 허용
- IAM role/profile: `review-catalog-platform-ec2-role` / `review-catalog-platform-ec2-profile`
- private, versioning-enabled artifact bucket: `review-catalog-platform-deploy-161088569936-20260805`
- artifact: `releases/0.3.0-r1/review-catalog-platform.tar.gz`
- checksum: 같은 prefix의 `SHA256SUMS`와 S3 object checksum에서 확인

SSH key와 inbound 22번 포트는 없다. EC2 IAM role은 Systems Manager core policy와 위 bucket의 object read/list 권한만 갖는다. FastAPI와 Airflow는 각각 host loopback 8000/8080에 bind하고, PostgreSQL은 Docker network에만 존재한다. EC2의 Nginx 80번은 CloudFront origin에서만 접근할 수 있고 public IP 직접 접속은 차단된다.

CloudFront 기본 도메인과 인증서가 viewer HTTPS와 HSTS/security headers를 제공한다. 애플리케이션 로그인은 없으므로 민감한 리뷰, credential, 개인정보는 입력하지 않는다. 소유 도메인이 준비되면 ACM 인증서를 distribution에 연결한다.

## 호스트 구조

```text
/opt/review-catalog-platform/
  releases/0.3.0/       # 최초 bootstrap application release
  releases/git-<sha>/   # CodeDeploy가 만든 immutable application release
  current -> releases/git-<sha>
  shared/.env           # EC2에서 생성한 root-only secrets; artifact에 없음
  shared/models/        # public Git에서 제외한 immutable SBERT snapshot

Docker named volumes
  catalog_data          # immutable releases와 DuckDB snapshots
  postgres_data         # pipeline/release operational metadata
  codex_auth            # Codex CLI device authentication
  airflow_logs
```

`deploy/aws/user-data.sh`가 Docker, Compose, SSM Agent, CodeDeploy Agent를 준비한다. CodeDeploy lifecycle hook은 Git SHA별 application directory를 만든 뒤 `deploy/aws/activate-release.sh`로 image build와 Compose 기동을 수행한다. `REVISION`의 Git SHA가 `report_generator` component version에도 들어가므로 코드 변경이 기존 version row를 덮어쓰지 않는다.

## 운영 확인

공개 endpoint:

```bash
curl --fail https://d1my2fe0yb8h33.cloudfront.net/api/health
curl --fail https://d1my2fe0yb8h33.cloudfront.net/api/products
curl --fail https://d1my2fe0yb8h33.cloudfront.net/api/catalog/releases/current
```

컨테이너 상태는 SSH 대신 SSM Run Command로 확인한다.

```bash
aws ssm send-command \
  --region ap-northeast-2 \
  --instance-ids i-0ada1de1769b3214d \
  --document-name AWS-RunShellScript \
  --parameters '{"commands":["cd /opt/review-catalog-platform/current && docker compose --project-name review-catalog-platform ps"]}'
```

Codex CLI 인증은 `codex_auth` volume에 저장된다. 일반 `docker compose down` 또는 image rebuild에서는 유지되며 `docker compose down -v`를 실행하면 삭제된다.

## 데이터 릴리스 검증

최초 정상 pipeline run은 `run_f748fa16167447209e422463fc30a574`이다.

- 이관 리뷰: 749
- 상품: 9
- 최초 release: `release-run_f748fa16167447209e422463fc30a574`
- DuckDB SHA-256: `bb2ae47673b31ba545c381f31363ff20a6c11fd1931916eb7fdfff512055e55e`
- 정적 Markdown 보고서: 9개; release manifest와 개별 SHA-256 일치

인스턴스 최초 부팅 시 자동 예약 run 하나가 macOS source permission 때문에 실패했다. 실패 run은 lineage를 위해 그대로 보존했고, permission 정규화 후 새로운 수동 run으로 전체 749건을 성공적으로 원자 배포했다.

Codex CLI device 인증 후 비민감 한국어 리뷰 1건을 공개 FastAPI에 제출해 전체 동적 경로도 검증했다.

- demo run: `run_fb8a95a2f80a4b77b735fa4939b50d5a`
- 현재 release: `release-run_fb8a95a2f80a4b77b735fa4939b50d5a`
- 누적 리뷰/Opinion Unit: 750 / 2,586
- 추출 결과: exact mapping 1개, 신규 candidate 1개, taxonomy 제외 1개
- report manifest: 정적 9개 + 동적 제안서 1개, 모든 SHA-256 일치
- 이전 release snapshot hash도 그대로 유지됨을 재검증

## GitHub Actions CI/CD 사용법

GitHub Actions workflow는 `.github/workflows/ci-cd.yml` 하나이며 `main` push 또는 수동 실행으로 동작한다.

```bash
git clone https://github.com/Experience-Driven-Product-Catalog/review-catalog-platform.git
cd review-catalog-platform

# 변경 후
git add <files>
git commit -m "describe the change"
git push origin main

# 실행 상태
gh run list --workflow CI/CD
gh run watch
```

수동 재배포는 새 코드를 만들지 않으며 현재 `main` SHA를 다시 검증·배포한다.

```bash
gh workflow run CI/CD --ref main
```

workflow는 다음 순서로 실행된다.

1. Gitleaks가 전체 committed history에서 credential을 검사한다.
2. Ruff, backend tests, frontend lint/build, Compose/AppSpec/hook 계약을 검사한다.
3. GitHub OIDC로 `review-catalog-platform-github-deploy-role`을 단기 assume한다.
4. tracked file과 `REVISION`만 포함한 CodeDeploy ZIP을 private versioned S3 `ci/<git-sha>/`에 업로드한다.
5. `review-catalog-platform-production` deployment를 생성하고 성공까지 기다린다.
6. CloudFront cache를 무효화하고 HTTPS API와 frontend의 `CODEDEPLOY` marker를 검증한다.

`.env`, `.env.*`, `.aws/`, `.codex/`, private key 형식, runtime `data/`, 447MiB model binary는 `.gitignore`에서 제외한다. 실제 EC2 secret은 `/opt/review-catalog-platform/shared/.env`에 mode 600으로만 존재한다. GitHub repository secret은 사용하지 않는다.

## 배포 갱신 내부 절차

1. GitHub Actions가 secret scan과 test/build를 통과시킨다.
2. private S3 bucket의 immutable Git SHA prefix에 CodeDeploy bundle과 SHA-256을 올린다.
3. CodeDeploy Agent가 `appspec.yml` lifecycle hook을 실행한다.
4. `releases/git-<sha>`에서 Docker images를 build하고 기존 named volumes로 services를 재생성한다.
5. localhost API, frontend marker, Codex 인증, container health를 검증한다.
6. 성공한 directory만 `current` symlink로 전환한다. 실패 시 deployment group이 이전 정상 revision을 자동 재배포한다.
7. GitHub Actions가 CloudFront cache를 무효화하고 HTTPS 반영까지 확인한다.

## 현재 온디맨드 비용 기준

2026-08-05 AWS Price List API의 서울 리전 정가 기준이다.

- `t4g.large`: USD 0.0832/hour, 730시간 기준 약 USD 60.74/month
- gp3 40 GiB: USD 0.0912/GB-month, 약 USD 3.65/month
- in-use public IPv4: USD 0.005/hour, 약 USD 3.65/month
- 합계 약 USD 68.03/month + CloudFront/S3 요청·저장, 데이터 전송, 세금

데모를 사용하지 않을 때 EC2를 stop하면 compute 요금은 멈추지만 EBS, Elastic IP와 S3 요금은 계속 발생한다. Elastic IP를 release하면 현재 URL은 바뀌므로 리소스 종료와 함께 수행한다.

## 종료 순서

비용을 완전히 멈추려면 먼저 필요한 PostgreSQL/catalog volume을 백업한 뒤 instance, EBS, Elastic IP, S3 artifact, instance profile/role, security group, route table, internet gateway, subnet, VPC 순으로 제거한다. 현재 명령은 문서화만 하며 자동 삭제 스크립트는 제공하지 않는다.
