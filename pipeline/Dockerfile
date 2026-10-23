FROM python:3.12-slim

WORKDIR /app

COPY pipeline/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY pipeline/ /app/pipeline/

CMD ["python", "-m", "pipeline.bronze.ingest"]
