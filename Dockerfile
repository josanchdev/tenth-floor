FROM python:3.12-slim

WORKDIR /app

# System deps: gcc for native extensions, curl for healthchecks, sqlite3 for reset-db
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc git curl sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies before copying source (better layer caching)
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir -e ".[dev]"

# Copy source after deps so code changes don't bust the pip cache
COPY src/ src/
COPY config/ config/
COPY db/ db/

# Data and logs are bind-mounted at runtime (./data, ./logs)
RUN mkdir -p data logs

CMD ["python", "-m", "tenth_floor.main"]
