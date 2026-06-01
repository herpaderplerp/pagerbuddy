FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml README.md ./
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "pagerbuddy.main:app", "--host", "0.0.0.0", "--port", "8000"]
