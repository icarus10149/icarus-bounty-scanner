#!/bin/bash
set -e

echo "Removing old container (if any)..."
docker rm -f icarus-bounty 2>/dev/null || true

echo "Creating local directories..."
mkdir -p logs output cache

echo "Building image..."
docker build -t icarus-bounty .

echo "Starting scanner..."
docker run -it --rm \
  --name icarus-bounty \
  -v "$(pwd)/logs":/app/logs \
  -v "$(pwd)/output":/app/output \
  -v "$(pwd)/cache":/app/cache \
  icarus-bounty