FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV LLM_BROKER_HOST=0.0.0.0
ENV LLM_BROKER_PORT=47831
ENV LLM_BROKER_AUTH_STORE_PATH=/data/auth/session.json
ENV LLM_BROKER_DISABLE_KEYRING=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --no-cache-dir .

VOLUME ["/data"]

EXPOSE 47831

CMD ["llm-broker", "serve", "--host", "0.0.0.0", "--port", "47831"]
