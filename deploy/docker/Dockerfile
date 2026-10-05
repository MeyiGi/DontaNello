FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY assistant.py ./assistant.py
COPY src ./src
COPY config ./config

RUN python -m pip install --no-cache-dir --disable-pip-version-check .

ENTRYPOINT ["python", "/app/assistant.py"]
