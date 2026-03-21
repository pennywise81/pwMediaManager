FROM python:3.11-slim

# Runtime deps for the managed scripts (bash, curl, python3 already in base)
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl ca-certificates \
  && rm -rf /var/lib/apt/lists/*
# Note: Docker CLI is provided via host bind-mount at runtime:
#   -v /usr/bin/docker:/usr/bin/docker:ro
#   -v /var/run/docker.sock:/var/run/docker.sock

WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .

ENV LOGS_DIR=/logs

EXPOSE 8080
CMD ["python", "app.py"]
