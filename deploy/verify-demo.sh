#!/usr/bin/env bash
# Read-only smoke check for a deployed ProofLoop instance.
# Usage: bash deploy/verify-demo.sh http://127.0.0.1:8000
set -euo pipefail

BASE="${1:-http://127.0.0.1:8000}"

echo "health:"
curl -fsS "$BASE/health"
echo
echo "containment:"
curl -fsS "$BASE/api/containment"
echo
