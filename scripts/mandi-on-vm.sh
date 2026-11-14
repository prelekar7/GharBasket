#!/usr/bin/env bash
# Run the daily mandi price collector (backend/training jobs/collect_mandi) on a small Linux VM in
# India: api.data.gov.in refuses connections from outside India (GitHub's runners, Render's
# Singapore), and a laptop misses days. Staging's collector is an Oracle Cloud Always Free VM in
# Mumbai (VM.Standard.E2.1.Micro, Ubuntu 24.04; handbook 12).
#
# From the Mac, with the VM's address and SSH key:
#   scripts/mandi-on-vm.sh setup  <ip> <key>   # install git, uv, swap, the repo and a daily timer
#   infisical run --env=staging -- scripts/mandi-on-vm.sh env <ip> <key>   # the job's secrets
#   scripts/mandi-on-vm.sh run    <ip> <key>   # collect now and show the log
#   scripts/mandi-on-vm.sh status <ip> <key>   # the timer and the last runs
#
# On the VM the timer runs `mandi-on-vm.sh collect` at 18:45 IST (13:15 UTC); a run missed while
# the VM was down runs at boot (Persistent=true). Each run pulls main first, so a merged fix
# reaches the VM without a redeploy. The secrets live in ~/.config/gharbasket/mandi.env (mode 600,
# only what this job needs); HC_PING_URL there, if set, is pinged on success and on failure.
set -euo pipefail

REPO_URL="https://github.com/prelekar7/GharBasket.git"
REMOTE_DIR="GharBasket"
ENV_FILE=".config/gharbasket/mandi.env"
UNIT="gharbasket-mandi"

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
    # MANDI_REF: the branch the VM follows (default main; another branch only to try a change).
    ssh_to "$2" "$3" "MANDI_REF=${MANDI_REF:-main} bash -s" <<'REMOTE'
set -euo pipefail
# 1 GB of RAM: a swap file keeps `uv sync` and the import of the API's modules from being killed.
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
# build-essential: statsforecast (a training dependency) builds from source on this image.
sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl build-essential >/dev/null
command -v uv >/dev/null || [ -x "$HOME/.local/bin/uv" ] || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
export PATH="$HOME/.local/bin:$PATH"
[ -d GharBasket/.git ] || git clone -q https://github.com/prelekar7/GharBasket.git
git -C GharBasket fetch -q origin && git -C GharBasket checkout -q -B "$MANDI_REF" "origin/$MANDI_REF"
cd GharBasket && uv sync -q --frozen --no-dev --package gharbasket-training
mkdir -p "$HOME/.config/gharbasket" && chmod 700 "$HOME/.config/gharbasket"
sudo tee /etc/systemd/system/gharbasket-mandi.service >/dev/null <<UNIT
[Unit]
Description=GharBasket mandi price collector (data.gov.in -> R2 lake, price index -> staging API)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/GharBasket
ExecStart=/home/ubuntu/GharBasket/scripts/mandi-on-vm.sh collect
TimeoutStartSec=30min
UNIT
sudo tee /etc/systemd/system/gharbasket-mandi.timer >/dev/null <<UNIT
[Unit]
Description=Collect today's mandi prices at 18:45 IST

[Timer]
OnCalendar=*-*-* 13:15:00 UTC
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
UNIT
sudo systemctl daemon-reload && sudo systemctl enable --now gharbasket-mandi.timer >/dev/null
echo "set up: next run $(systemctl show gharbasket-mandi.timer -p NextElapseUSecRealtime --value)"
[ -s "$HOME/.config/gharbasket/mandi.env" ] || echo "no secrets yet: run 'scripts/mandi-on-vm.sh env' from the Mac"
REMOTE
    ;;
  env)
    need_host "$@"
    # Runs under `infisical run --env=staging`: the values go over SSH into a mode-600 file and are
    # never printed or put on a command line. The training jobs' own R2 token when set, else the API's.
    : "${DATA_GOV_IN_API_KEY:?run under: infisical run --env=staging -- $0 env <ip> <key>}"
    : "${JOBS_TOKEN:?JOBS_TOKEN is missing}" "${S3_ENDPOINT_URL:?S3_ENDPOINT_URL is missing}"
    {
      printf 'DATA_GOV_IN_API_KEY=%s\n' "$DATA_GOV_IN_API_KEY"
      printf 'JOBS_TOKEN=%s\n' "$JOBS_TOKEN"
      printf 'API_URL=%s\n' "${STAGING_API_URL:-https://api.gharbasket.co.in}"
      printf 'S3_ENDPOINT_URL=%s\n' "$S3_ENDPOINT_URL"
      printf 'S3_ACCESS_KEY=%s\n' "${TRAINING_S3_ACCESS_KEY:-$S3_ACCESS_KEY}"
      printf 'S3_SECRET_KEY=%s\n' "${TRAINING_S3_SECRET_KEY:-$S3_SECRET_KEY}"
      printf 'S3_REGION=auto\nS3_BUCKET_LAKE=gb-staging-lake\nS3_BUCKET_MLFLOW=gb-staging-mlflow\n'
      [ -z "${MANDI_HC_PING_URL:-}" ] || printf 'HC_PING_URL=%s\n' "$MANDI_HC_PING_URL"
    } | ssh_to "$2" "$3" "umask 077 && mkdir -p ~/.config/gharbasket && cat > ~/$ENV_FILE"
    echo "secrets written to ~/$ENV_FILE on $2 (mode 600)"
    ;;
  run)
    need_host "$@"
    ssh_to "$2" "$3" "sudo systemctl start $UNIT.service; journalctl -u $UNIT.service -n 25 --no-pager -o cat"
    ;;
  status)
    need_host "$@"
    ssh_to "$2" "$3" "systemctl list-timers $UNIT.timer --no-pager; journalctl -u $UNIT.service -n 8 --no-pager -o short-iso"
    ;;
  collect)
    # On the VM, from the timer: the newest main, then the collector with the job's secrets.
    cd "$(dirname "$0")/.."
    export PATH="$HOME/.local/bin:$PATH"
    git pull -q --ff-only origin "$(git branch --show-current)" || echo "git pull failed; collecting with the checked-out code"
    uv sync -q --frozen --no-dev --package gharbasket-training
    set -a
    # shellcheck disable=SC1090
    . "$HOME/$ENV_FILE"
    set +a
    ping() { [ -z "${HC_PING_URL:-}" ] || curl -fsS -m 10 --retry 3 -o /dev/null "$HC_PING_URL$1" || true; }
    ping /start
    if uv run --frozen --no-dev --package gharbasket-training \
      python -m gharbasket_training.jobs.collect_mandi --push-index; then
      ping ""
    else
      status=$?
      ping /fail
      exit "$status"
    fi
    ;;
  *)
    echo "usage: $0 setup|env|run|status <ip> <ssh key file>   (collect runs on the VM)" >&2
    exit 2
    ;;
esac
