#!/usr/bin/env bash
# Run staging's scheduled jobs from the Oracle VM in Mumbai (ADR-0032): scripts/jobs_scheduler.py
# as the systemd service `gharbasket-jobs`, calling the `vm` entries of infra/jobs/schedule.yaml.
# It replaced cron-job.org, whose calls Render's edge turned away (503/429) on most evenings.
#
# From the Mac, with the VM's address and SSH key (the same VM as the mandi collector, handbook 12):
#   scripts/jobs-on-vm.sh setup  <ip> <key>   # python3-yaml, the repo if missing, the service
#   infisical run --env=staging -- scripts/jobs-on-vm.sh env <ip> <key>   # API_URL, JOBS_TOKEN, HC_*
#   scripts/jobs-on-vm.sh status <ip> <key>   # the service and its last calls
#   scripts/jobs-on-vm.sh due    <ip> <key> 2026-11-05T21:00   # the jobs due at an IST minute
#
# The service starts again on failure and at boot; it pulls the checkout's branch every 15
# minutes and restarts itself when its own code changed, so a merged schedule reaches it without a
# redeploy. The secrets live in ~/.config/gharbasket/jobs.env (mode 600, only what it needs).
set -euo pipefail

ENV_FILE=".config/gharbasket/jobs.env"
UNIT="gharbasket-jobs"

ssh_to() {  # ssh_to <ip> <key> <command...>
  local ip=$1 key=$2
  shift 2
  ssh -i "$key" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 "ubuntu@$ip" "$@"
}

need_host() {
  [ $# -ge 2 ] || { echo "usage: $0 $1 <ip> <ssh key file>" >&2; exit 2; }
}

case "${1:-}" in
  setup)
    need_host "$@"
    # JOBS_REF: the branch the VM follows when it clones (default main). An existing checkout
    # (the mandi collector's) is kept on its branch.
    ssh_to "$2" "$3" "JOBS_REF=${JOBS_REF:-main} bash -s" <<'REMOTE'
set -euo pipefail
sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3-yaml tzdata >/dev/null
[ -d GharBasket/.git ] || git clone -q -b "$JOBS_REF" https://github.com/prelekar7/GharBasket.git
git -C GharBasket pull -q --ff-only origin "$(git -C GharBasket branch --show-current)"
mkdir -p "$HOME/.config/gharbasket" && chmod 700 "$HOME/.config/gharbasket"
sudo tee /etc/systemd/system/gharbasket-jobs.service >/dev/null <<UNIT
[Unit]
Description=GharBasket scheduled jobs (infra/jobs/schedule.yaml vm entries -> staging API)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/GharBasket
EnvironmentFile=-/home/ubuntu/.config/gharbasket/jobs.env
ExecStart=/usr/bin/python3 -u /home/ubuntu/GharBasket/scripts/jobs_scheduler.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload && sudo systemctl enable gharbasket-jobs.service >/dev/null
if [ -s "$HOME/.config/gharbasket/jobs.env" ]; then
  sudo systemctl restart gharbasket-jobs.service && echo "set up and running"
else
  echo "set up; no secrets yet: run 'scripts/jobs-on-vm.sh env' from the Mac"
fi
REMOTE
    ;;
  env)
    need_host "$@"
    # Runs under `infisical run --env=staging`: the values go over SSH into a mode-600 file and are
    # never printed or put on a command line. Then the service restarts with them.
    : "${JOBS_TOKEN:?run under: infisical run --env=staging -- $0 env <ip> <key>}"
    {
      printf 'JOBS_TOKEN=%s\n' "$JOBS_TOKEN"
      printf 'API_URL=%s\n' "${STAGING_API_URL:-https://api.gharbasket.co.in}"
      # The healthchecks.io ping URLs (ADR-0033), each only when Infisical has it.
      for name in HC_PING_URL HC_CUTOFF_URL HC_OPS_URL; do
        [ -z "${!name:-}" ] || printf '%s=%s\n' "$name" "${!name}"
      done
    } | ssh_to "$2" "$3" "umask 077 && mkdir -p ~/.config/gharbasket && cat > ~/$ENV_FILE && sudo systemctl restart $UNIT.service"
    echo "secrets written to ~/$ENV_FILE on $2 (mode 600); $UNIT restarted"
    ;;
  status)
    need_host "$@"
    ssh_to "$2" "$3" "systemctl status $UNIT.service --no-pager -n 0 | head -5; journalctl -u $UNIT.service -n 25 --no-pager -o cat"
    ;;
  due)
    need_host "$@"
    [ $# -ge 4 ] || { echo "usage: $0 due <ip> <key> <IST minute, e.g. 2026-11-05T21:00>" >&2; exit 2; }
    ssh_to "$2" "$3" "python3 GharBasket/scripts/jobs_scheduler.py --due '$4'"
    ;;
  *)
    echo "usage: $0 setup|env|status|due <ip> <ssh key file>" >&2
    exit 2
    ;;
esac
