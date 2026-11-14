#!/usr/bin/env bash
# One-time setup of the BurnBeat bot on the Google Cloud VM (Ubuntu). Run from the repo root:
#   bash deploy/setup.sh
# Then put the secrets into /etc/burnbeat-bot.env (the script creates a template) and run:
#   sudo systemctl restart burnbeat-bot
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"

sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv git
python3 -m venv "$REPO/.venv"
"$REPO/.venv/bin/pip" install -q -r "$REPO/requirements.txt"

if [ ! -f /etc/burnbeat-bot.env ]; then
  sudo install -m 600 -o "$USER_NAME" /dev/null /etc/burnbeat-bot.env
  printf 'TELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\n' | sudo tee /etc/burnbeat-bot.env >/dev/null
  echo ">>> Заполните /etc/burnbeat-bot.env (токен и ID канала): sudo nano /etc/burnbeat-bot.env"
fi

for unit in "$REPO"/deploy/systemd/*; do
  sed -e "s#@REPO@#$REPO#g" -e "s#@USER@#$USER_NAME#g" "$unit" | sudo tee "/etc/systemd/system/$(basename "$unit")" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now burnbeat-digest.timer burnbeat-alerts.timer
sudo systemctl enable burnbeat-bot.service
grep -q '^TELEGRAM_BOT_TOKEN=.\+' /etc/burnbeat-bot.env && sudo systemctl restart burnbeat-bot.service || true
systemctl list-timers 'burnbeat-*' --no-pager
