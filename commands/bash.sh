#!/usr/bin/env bash
#
# Open a shell inside the running monitor container.
#
set -euo pipefail
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/_common.sh"

CID="$(container_id)"
[ -n "$CID" ] || { echo "${CONTAINER} is not running. Run ./commands/build.sh" >&2; exit 1; }
docker exec -it "$CID" bash
