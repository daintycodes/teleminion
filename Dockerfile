# TeleMinion V2 - Telegram to MinIO Pipeline
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies including curl for health checks and pg_dump for backups
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create downloads directory
RUN mkdir -p /tmp/downloads

# Create non-root user
RUN useradd -m -u 1000 teleminio && \
    chown -R teleminio:teleminio /app /tmp/downloads
USER teleminio

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
