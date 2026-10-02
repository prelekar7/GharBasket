# MLflow tracking server and model registry (backend store: Postgres, artifacts: S3-compatible).
FROM python:3.13-slim

RUN pip install --no-cache-dir mlflow==3.16.1 psycopg2-binary==2.9.10 boto3==1.40.0

EXPOSE 5000
CMD ["mlflow", "server", "--host", "0.0.0.0", "--port", "5000"]
