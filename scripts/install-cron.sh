#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME="planka-checker-daily"
COMPOSE_FILE="compose.yml"
SERVICE_NAME="planka-checker"
SUBCOMMAND="daily"
SUBCOMMAND_FLAGS="--summarize"
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WRAPPER="/usr/local/bin/${SCRIPT_NAME}"
CRON_FILE="/etc/cron.daily/${SCRIPT_NAME}"

usage() {
    cat <<EOF
Usage: $(basename "$0") [install|uninstall|status]

Commands:
    install     Install the wrapper script and daily cron job (requires sudo)
    uninstall   Remove the wrapper script and daily cron job (requires sudo)
    status      Show current installation status
EOF
}

install() {
    cat <<EOF
This will install:
  - Wrapper: ${WRAPPER}
  - Cron job: ${CRON_FILE}

The cron job will run 'docker compose run --rm ${SERVICE_NAME} ${SUBCOMMAND} ${SUBCOMMAND_FLAGS}'
every day from ${PROJECT_DIR}.

You will be prompted for your sudo password.
EOF
    sudo -v

    sudo tee "${WRAPPER}" >/dev/null <<WRAPPER_EOF
#!/usr/bin/env bash
set -euo pipefail
exec /usr/bin/docker compose \\
    --project-directory "${PROJECT_DIR}" \\
    -f "${PROJECT_DIR}/${COMPOSE_FILE}" \\
    run --rm ${SERVICE_NAME} ${SUBCOMMAND} ${SUBCOMMAND_FLAGS}
WRAPPER_EOF
    sudo chmod 0755 "${WRAPPER}"

    sudo tee "${CRON_FILE}" >/dev/null <<CRON_EOF
#!/bin/sh
# Runs the planka-checker container once per day and produces a daily report.
exec ${WRAPPER}
CRON_EOF
    sudo chmod 0755 "${CRON_FILE}"

    echo
    echo "Installed."
    echo "Test it manually with: sudo ${WRAPPER}"
    echo "Or wait for ${CRON_FILE} to be picked up by cron.daily."
}

uninstall() {
    sudo -v
    removed=0
    if [ -e "${WRAPPER}" ]; then
        sudo rm -f "${WRAPPER}"
        echo "Removed ${WRAPPER}"
        removed=1
    fi
    if [ -e "${CRON_FILE}" ]; then
        sudo rm -f "${CRON_FILE}"
        echo "Removed ${CRON_FILE}"
        removed=1
    fi
    if [ "${removed}" -eq 0 ]; then
        echo "Nothing to remove."
    fi
}

status() {
    echo "Project directory: ${PROJECT_DIR}"
    echo "Wrapper:           ${WRAPPER} $( [ -e "${WRAPPER}" ] && echo '(installed)' || echo '(missing)' )"
    echo "Cron job:          ${CRON_FILE} $( [ -e "${CRON_FILE}" ] && echo '(installed)' || echo '(missing)' )"
}

case "${1:-}" in
    install)    install ;;
    uninstall)  uninstall ;;
    status)     status ;;
    -h|--help|help|"") usage ;;
    *) usage; exit 1 ;;
esac
