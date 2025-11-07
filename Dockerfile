FROM python:3.12-slim

WORKDIR /app

# --- SYSTEM DEPS ---
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates git curl unzip \
    p7zip-full gcc make xz-utils && \
    rm -rf /var/lib/apt/lists/*

# --- NUCLEI ---
RUN curl -fsSL https://github.com/projectdiscovery/nuclei/releases/download/v3.4.10/nuclei_3.4.10_linux_amd64.zip -o nuclei.zip && \
    unzip nuclei.zip -d /usr/local/bin && rm nuclei.zip && \
    chmod +x /usr/local/bin/nuclei && \
    nuclei -update-templates

# --- ANSIBLE COLLECTION (pre-install to avoid race) ---
RUN pip install --no-cache-dir ansible && \
    ansible-galaxy collection install community.general --force

# --- PYTHON DEPS ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- COPY CODE ---
COPY . .

# --- DIRS ---
RUN mkdir -p /app/logs /app/output /app/cache && \
    chmod 777 /app/logs /app/output /app/cache

# --- BBOT INIT ---
RUN touch /app/src/__init__.py /app/src/scanner/__init__.py

CMD ["python", "-m", "src.main"]