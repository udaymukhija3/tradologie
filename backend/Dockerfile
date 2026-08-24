FROM python:3.12.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AUTO_CREATE_SCHEMA=false \
    AUTO_SEED=false \
    RUN_MIGRATIONS=false

WORKDIR /app
RUN addgroup --system tradevoice && adduser --system --ingroup tradevoice tradevoice
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh && chown -R tradevoice:tradevoice /app

USER tradevoice
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/live', timeout=2)"]
ENTRYPOINT ["./entrypoint.sh"]

FROM runtime AS test
USER root
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests ./tests
USER tradevoice
ENTRYPOINT []
CMD ["python", "-m", "pytest", "-q"]
