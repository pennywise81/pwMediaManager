FROM python:3.11-slim

# Runtime deps for the managed scripts (bash, curl, python3 already in base)
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl ca-certificates docker.io \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .

ENV LOGS_DIR=/logs

EXPOSE 8080
CMD ["python", "app.py"]
