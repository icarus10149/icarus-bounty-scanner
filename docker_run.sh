#!/bin/bash
set -e

echo "Removing old container..."
docker rm -f icarus-bounty 2>/dev/null || true

echo "Creating local directories..."
mkdir -p config logs output cache

echo "Building image..."
docker build -t icarus-bounty .

echo "Starting scanner..."
docker run -it --rm \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/output:/app/output \
  -v $(pwd)/cache:/app/cache \
  --name icarus-bounty \
  icarus-bounty