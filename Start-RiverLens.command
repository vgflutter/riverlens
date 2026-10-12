#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Installa Python 3.10 o successivo, poi riapri questo file."
  read -r -p "Premi Invio per chiudere."
  exit 1
fi
python3 server.py
