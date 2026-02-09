# Single image used for BOTH:
# - Cloud Run Service (uvicorn)
# - Cloud Run Job (python -m scheduler_orchestrator.job)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps (tiny set)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Install uv (fast dependency installer)
RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md /app/
COPY src/ /app/src/

RUN uv pip compile pyproject.toml -o requirements.txt
RUN uv pip install --system -r requirements.txt

# Default: run the SERVICE
ENV PYTHONPATH=/app/src
ENV PORT=8080
CMD ["uvicorn", "scheduler_orchestrator.service:app", "--host", "0.0.0.0", "--port", "8080"]
