#!/bin/bash
# Build the web app on this machine, copy the tracked repo files to the Spark, restart the service.
#   bash deploy/spark/deploy.sh            (from the repo root; needs `ssh compute`)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
(cd web && npm ci --silent && npm run build)
ssh compute 'mkdir -p ~/epics-app/repo && rm -rf ~/epics-app/repo/web/dist'
{ git ls-files -z --cached --others --exclude-standard; find web/dist -type f -print0; } | \
    rsync -a --from0 --files-from=- ./ compute:epics-app/repo/
ssh compute 'bash ~/epics-app/repo/deploy/spark/up.sh'
