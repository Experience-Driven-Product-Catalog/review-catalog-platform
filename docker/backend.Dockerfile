FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/project
COPY backend /opt/project/backend
COPY config /opt/project/config
COPY assets/resume.md /opt/project/assets/resume.md
COPY README.md /opt/project/README.md
RUN pip install --no-cache-dir /opt/project/backend

USER 10001:0
EXPOSE 8000
CMD ["uvicorn", "review_catalog.main:app", "--host", "0.0.0.0", "--port", "8000"]
