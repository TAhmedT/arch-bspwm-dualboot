#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
if [[ "${1:-}" == prepare-zfs ]]; then
  exec bash prepare-zfs.sh
fi
command -v python3 >/dev/null || { echo 'Нужен python: pacman -Sy --needed python git'; exit 1; }
exec python3 arch_setup.py "$@"
