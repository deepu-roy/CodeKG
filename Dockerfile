FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src src
# Install CPU-only PyTorch first to avoid pulling 500MB+ CUDA libraries
# that are unused in Docker (no GPU passthrough).
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -e .

COPY tests tests

EXPOSE 8765

CMD ["code-kg", "mcp", "http"]
