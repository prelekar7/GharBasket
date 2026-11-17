#!/usr/bin/env bash
# Run the scheduled jobs from the Oracle VM in Mumbai (ADR-0032): scripts/jobs_scheduler.py as a
# systemd service, calling the `vm` entries of infra/jobs/schedule.yaml.
# It replaced cron-job.org, whose calls Render's edge turned away (503/429) on most evenings.
#
# From the Mac, with the VM's address and SSH key (the same VM as the mandi collector, handbook 12):
#   scripts/jobs-on-vm.sh setup  <ip> <key>   # python3-yaml, the repo if missing, the service
#   infisical run --env=staging -- scripts/jobs-on-vm.sh env <ip> <key>   # API_URL, JOBS_TOKEN, HC_*
#   scripts/jobs-on-vm.sh status <ip> <key>   # the service and its last calls
#   scripts/jobs-on-vm.sh due    <ip> <key> 2026-11-05T21:00   # the jobs due at an IST minute
#
# Targets (ADR-0036, handbook 12 §12.9): a target name after the command picks the deployment.
# `staging` is the default, so the commands above are staging's (service `gharbasket-jobs`).
# Production is opt-in, the service `gharbasket-jobs-prod`:
#   scripts/jobs-on-vm.sh setup prod <ip> <key>   # installs it; it does not start yet
#   infisical run --env=prod -- scripts/jobs-on-vm.sh env prod <ip> <key>   # PROD_API_URL, JOBS_TOKEN, HC_*
#   scripts/jobs-on-vm.sh status prod <ip> <key>
# Each target has its own service, env file and state file; they share the checkout and the
# schedule. Production's service stays inert until its env file exists.
#
# A service starts again on failure and at boot; it pulls the checkout's branch every 15
# minutes and restarts itself when its own code changed, so a merged schedule reaches it without a
# redeploy. The secrets live in ~/.config/gharbasket/jobs.env (staging) and jobs-prod.env
# (production), mode 600, only what the service needs.
set -euo pipefail

cmd=${1:-}
[ $# -eq 0 ] || shift
target=staging
case "${1:-}" in
  staging | prod)
    target=$1
    shift
    ;;
esac

if [ "$target" = staging ]; then
  ENV_FILE=".config/gharbasket/jobs.env"
  UNIT="gharbasket-jobs"
  ARGS=""
  NAMED=""
else
  ENV_FILE=".config/gharbasket/jobs-$target.env"
  UNIT="gharbasket-jobs-$target"
  ARGS=" --target $target"
  NAMED=" $target"
fi

ssh_to() {  # ssh_to <ip> <key> <command...>
  local ip=$1 key=$2
  shift 2
  ssh -i "$key" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 "ubuntu@$ip" "$@"
}

need_host() {
  [ $# -ge 2 ] || { echo "usage: $0 $cmd [staging|prod] <ip> <ssh key file>" >&2; exit 2; }
}

case "$cmd" in
  setup)
    need_host "$@"
    # JOBS_REF: the branch the VM follows when it clones (default main). An existing checkout
    # (the mandi collector's) is kept on its branch.
    ssh_to "$1" "$2" "JOBS_REF=${JOBS_REF:-main} TARGET=$target UNIT=$UNIT ENV_FILE=$ENV_FILE ARGS='$ARGS' NAMED='$NAMED' bash -s" <<'REMOTE'
set -euo pipefail
sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3-yaml tzdata >/dev/null
[ -d GharBasket/.git ] || git clone -q -b "$JOBS_REF" https://github.com/prelekar7/GharBasket.git
git -C GharBasket pull -q --ff-only origin "$(git -C GharBasket branch --show-current)"
mkdir -p "$HOME/.config/gharbasket" && chmod 700 "$HOME/.config/gharbasket"
# Staging's unit is the one it always had. Another target's waits for its env file
# (ConditionFileNotEmpty): until `env <target>` writes it, systemd skips the service.
CONDITION=""
[ "$TARGET" = staging ] || CONDITION=$'\n'"ConditionFileNotEmpty=/home/ubuntu/$ENV_FILE"
sudo tee "/etc/systemd/system/$UNIT.service" >/dev/null <<UNIT
[Unit]
Description=GharBasket scheduled jobs (infra/jobs/schedule.yaml vm entries -> $TARGET API)
Wants=network-online.target
After=network-online.target$CONDITION

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/GharBasket
EnvironmentFile=-/home/ubuntu/$ENV_FILE
ExecStart=/usr/bin/python3 -u /home/ubuntu/GharBasket/scripts/jobs_scheduler.py$ARGS
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload && sudo systemctl enable "$UNIT.service" >/dev/null
if [ -s "$HOME/$ENV_FILE" ]; then
  sudo systemctl restart "$UNIT.service" && echo "$UNIT set up and running"
else
  echo "$UNIT set up; no secrets yet: run 'scripts/jobs-on-vm.sh env$NAMED' from the Mac"
fi
REMOTE
    ;;
  env)
    need_host "$@"
    # Runs under `infisical run --env=<target>`: the values go over SSH into a mode-600 file and
    # are never printed or put on a command line. Then the service restarts with them.
    : "${JOBS_TOKEN:?run under: infisical run --env=$target -- $0 env$NAMED <ip> <key>}"
    if [ "$target" = staging ]; then
      api_url=${STAGING_API_URL:-https://api.gharbasket.co.in}
    else
      # Production has no default address (its name moves at the domain move, handbook 12
      # §12.9), and staging's values must never reach production's file.
      api_url=${PROD_API_URL:?set PROD_API_URL in Infisical prod to the production API base URL}
      if [ -n "${APP_ENV:-}" ] && [ "$APP_ENV" != prod ]; then
        echo "APP_ENV is '$APP_ENV', not prod: run under infisical run --env=prod" >&2
        exit 2
      fi
      if [ -n "${STAGING_API_URL:-}" ] && [ "${api_url%/}" = "${STAGING_API_URL%/}" ]; then
        echo "PROD_API_URL is staging's address; refusing" >&2
        exit 2
      fi
      case "$api_url" in
        *staging* | *gharbasket-api.onrender.com*)
          echo "PROD_API_URL ($api_url) looks like staging's address; refusing" >&2
          exit 2
          ;;
        https://*) ;;
        *)
          echo "PROD_API_URL must be an https:// URL" >&2
          exit 2
          ;;
      esac
    fi
    {
      printf 'JOBS_TOKEN=%s\n' "$JOBS_TOKEN"
      printf 'API_URL=%s\n' "$api_url"
      # The healthchecks.io ping URLs (ADR-0033), each only when Infisical has it. Infisical
      # `prod` holds production's checks under the same names (prod-scheduler, prod-cutoff, prod-ops).
      for name in HC_PING_URL HC_CUTOFF_URL HC_OPS_URL; do
        [ -z "${!name:-}" ] || printf '%s=%s\n' "$name" "${!name}"
      done
    } | ssh_to "$1" "$2" "test -f /etc/systemd/system/$UNIT.service || { echo '$UNIT is not installed: run setup$NAMED first' >&2; exit 1; }; umask 077 && mkdir -p ~/.config/gharbasket && cat > ~/$ENV_FILE && sudo systemctl restart $UNIT.service"
    echo "secrets written to ~/$ENV_FILE on $1 (mode 600); $UNIT restarted"
    ;;
  status)
    need_host "$@"
    ssh_to "$1" "$2" "systemctl status $UNIT.service --no-pager -n 0 | head -5; journalctl -u $UNIT.service -n 25 --no-pager -o cat"
    ;;
  due)
    need_host "$@"
    # Every target reads the same schedule, so the answer is the same for each.
    [ $# -ge 3 ] || { echo "usage: $0 due [staging|prod] <ip> <key> <IST minute, e.g. 2026-11-05T21:00>" >&2; exit 2; }
    ssh_to "$1" "$2" "python3 GharBasket/scripts/jobs_scheduler.py --due '$3'"
    ;;
  *)
    echo "usage: $0 setup|env|status|due [staging|prod] <ip> <ssh key file>" >&2
    exit 2
    ;;
esac
