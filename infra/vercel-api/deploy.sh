#!/usr/bin/env bash
# Deploy the Scout API to Vercel (Python serverless), since 2026-11-14 when Render suspended the free API.
# Copies the API and shared code next to the entry point, then deploys to the "scout-api" project.
# Usage (from the repo root, VERCEL_TOKEN in .env): bash infra/vercel-api/deploy.sh
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"; root="$(cd "$here/../.." && pwd)"
build="$(mktemp -d)"
cp -R "$here/api" "$here/requirements.txt" "$here/vercel.json" "$here/.python-version" "$build/"
cp -R "$root/packages/shared/src/scout_core" "$root/apps/api/src/scout_api" "$build/"
find "$build" -name __pycache__ -prune -exec rm -rf {} +
mkdir -p "$build/.vercel"
echo '{"projectId":"prj_8rDuHCCNs7WcYuHsU2BPLpWVLRU0","orgId":"team_VepiDlE8lg4ZlONtCbBuIlbh"}' > "$build/.vercel/project.json"
set -a; source "$root/.env"; set +a
(cd "$build" && npx -y vercel@latest deploy --prod --yes --token "$VERCEL_TOKEN")
