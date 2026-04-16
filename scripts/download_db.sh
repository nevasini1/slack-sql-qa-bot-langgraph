#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/langchain-ai/applied-ai-take-home-database.git"
TMP_DIR="./tmp/db-repo"
TARGET_DIR="${1:-./state}"
TARGET_FILE="${2:-app.db}"

mkdir -p "${TARGET_DIR}"
rm -rf "${TMP_DIR}"
mkdir -p "./tmp"
git clone --depth 1 "${REPO_URL}" "${TMP_DIR}"

DB_PATH="$(
python3 - <<'PY' "${TMP_DIR}"
import sys
from pathlib import Path

root = Path(sys.argv[1])
candidates = list(root.rglob("*.db")) + list(root.rglob("*.sqlite")) + list(root.rglob("*.sqlite3"))
if not candidates:
    raise SystemExit(1)
largest = max(candidates, key=lambda p: p.stat().st_size)
print(largest)
PY
)"

cp "${DB_PATH}" "${TARGET_DIR}/${TARGET_FILE}"
rm -rf "./tmp"
echo "Database copied to ${TARGET_DIR}/${TARGET_FILE}"
