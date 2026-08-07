FROM apache/airflow:3.3.0-python3.12

ARG AIRFLOW_VERSION=3.3.0
ARG CODEX_CLI_VERSION=0.146.0
ARG REVIEW_CATALOG_EXTRAS=""

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && npm install -g "@openai/codex@${CODEX_CLI_VERSION}" \
    && mkdir -p /home/airflow/.codex \
    && chown -R airflow:root /home/airflow/.codex \
    && chmod 2770 /home/airflow/.codex

USER airflow
RUN pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu \
      "torch==2.13.0+cpu"
COPY --chown=airflow:root backend /opt/project/backend
RUN pip install --no-cache-dir \
      "apache-airflow==${AIRFLOW_VERSION}" \
      "/opt/project/backend${REVIEW_CATALOG_EXTRAS}"
COPY --chown=airflow:root config /opt/project/config
COPY --chown=airflow:root scripts/init_airflow_pool.py /opt/project/scripts/init_airflow_pool.py
