#!/usr/bin/env bash
#
# Tail the monitor's logs.
#   ./commands/logs.sh              Follow.
#   ./commands/logs.sh --tail=50    Pass any docker logs flag.
#
set -euo pipefail
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/_common.sh"

CID="$(container_id any)"
[ -n "$CID" ] || { echo "No ${CONTAINER} container. Run ./commands/build.sh" >&2; exit 1; }
docker logs "${1:--f}" "$CID"
