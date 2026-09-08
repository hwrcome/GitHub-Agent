FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/home/app/.local/bin:${PATH}"

RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY agent_new.py ./
COPY tools ./tools
COPY skills ./skills
COPY alembic ./alembic
COPY alembic.ini docker-entrypoint.sh ./

RUN pip install --no-cache-dir . && \
    chmod +x /app/docker-entrypoint.sh && \
    chown -R app:app /app

USER app
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
