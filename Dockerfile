# ---- Build Stage ----
FROM python:3.12-slim AS builder
WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential gcc libssl-dev libffi-dev python3-dev git wget curl ca-certificates unzip && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# ---- Runtime Stage ----
FROM python:3.12-slim
WORKDIR /app

# Install runtime deps + sudo + core BBOT deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates git curl unzip sudo gcc make xz-utils p7zip-full && \
    rm -rf /var/lib/apt/lists/* && \
    echo "scanner ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/scanner

# Install Nuclei
RUN curl -fsSL https://github.com/projectdiscovery/nuclei/releases/download/v3.4.10/nuclei_3.4.10_linux_amd64.zip \
    -o /tmp/nuclei.zip && \
    unzip /tmp/nuclei.zip -d /usr/local/bin && \
    chmod +x /usr/local/bin/nuclei && \
    rm /tmp/nuclei.zip

# Copy Python deps
COPY --from=builder /root/.local /usr/local

# Copy app
COPY . .

# Create user + dirs
RUN useradd -m -s /bin/bash scanner && \
    mkdir -p /app/logs /app/output /app/cache /bbot && \
    chown -R scanner:scanner /app /bbot && \
    chmod 755 /app /bbot

# Switch to scanner
USER scanner

# Set BBOT home to /bbot (writable)
ENV BBOT_HOME=/bbot \
    XDG_CONFIG_HOME=/bbot/.config \
    XDG_CACHE_HOME=/bbot/.cache

# Create BBOT dirs
RUN mkdir -p /bbot/.config/bbot /bbot/.cache && \
    touch /app/src/__init__.py /app/src/scanner/__init__.py

ENV PYTHONPATH=/app \
    PATH="/usr/local/bin:${PATH}"

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD pgrep -f "python.*main.py" > /dev/null || exit 1

CMD ["sh", "-c", "nuclei -update-templates && python -u src/main.py"]