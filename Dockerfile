FROM python:3.11-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy dependency files first (layer cache)
COPY requirements.txt .

# Install dependencies — uv uses pre-built wheels so gcc is not needed
RUN uv pip install --system --no-cache -r requirements.txt

# Copy source code
COPY . .

EXPOSE 8002

# Default: run FastAPI server
# celery-worker and celery-beat override this via docker-compose command:
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8002", "--reload"]