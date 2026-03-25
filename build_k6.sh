#!/bin/bash
# Build custom K6 binary with xk6-sql and MS SQL driver
#
# Prerequisites: Go 1.21+ installed
# Installs xk6 if not present, then builds k6 with SQL Server support.

set -euo pipefail

echo "Building custom K6 binary with SQL Server support..."

# Install xk6 if not present
if ! command -v xk6 &> /dev/null; then
    echo "Installing xk6..."
    go install go.k6.io/xk6/cmd/xk6@latest
fi

# Build K6 with SQL extensions
xk6 build \
    --with github.com/grafana/xk6-sql \
    --with github.com/grafana/xk6-sql-driver-sqlserver \
    --output ./k6

echo ""
echo "Build complete: ./k6"
echo "Verify with: ./k6 version"
