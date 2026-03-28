#!/bin/bash
ACTION="${1:-list}"
KEY="$2"
VALUE="$3"
SERVICE="${RAILWAY_SERVICE:-cofounder}"

case "$ACTION" in
  list)
    railway variables -s "$SERVICE" --kv 2>&1
    ;;
  get)
    if [ -z "$KEY" ]; then
      echo "Usage: run.sh get KEY_NAME"
      exit 1
    fi
    railway variables -s "$SERVICE" --kv 2>&1 | grep "^${KEY}="
    ;;
  set)
    if [ -z "$KEY" ] || [ -z "$VALUE" ]; then
      echo "Usage: run.sh set KEY_NAME VALUE"
      exit 1
    fi
    railway variables -s "$SERVICE" --set "${KEY}=${VALUE}" 2>&1
    echo "Set ${KEY} on Railway"
    ;;
  *)
    echo "Usage: run.sh [list|get|set] [KEY] [VALUE]"
    ;;
esac
