#!/usr/bin/env bash
#
# Stop and remove the monitor container.
#
set -euo pipefail
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/_common.sh"

cd "$BASE_DIR"
compose -p "$PROJECT" down --remove-orphans
