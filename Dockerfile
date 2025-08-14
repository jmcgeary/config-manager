# Multi-stage Dockerfile for config service
FROM python:3.11-slim as builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Set working directory
WORKDIR /app

# Copy dependency files and source code
COPY pyproject.toml README.md ./
COPY config_service/ ./config_service/
COPY config_client/ ./config_client/

# Install dependencies in virtual environment
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN uv pip install -e .

# Production stage
FROM python:3.11-slim as production

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create non-root user
RUN groupadd -r configservice && useradd -r -g configservice configservice

# Set working directory
WORKDIR /app

# Copy application code from builder stage
COPY --from=builder /app/config_service/ ./config_service/
COPY --from=builder /app/config_client/ ./config_client/

# Change ownership to non-root user
RUN chown -R configservice:configservice /app
USER configservice

# Set environment variables
ENV HOST=0.0.0.0
ENV PORT=8080
ENV LOG_LEVEL=info
ENV ETCD_ENDPOINTS=localhost:2379

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Expose port
EXPOSE 8080

# Run the service
CMD ["python", "-m", "config_service.server"]