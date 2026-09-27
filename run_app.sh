#!/usr/bin/env bash
# Launch the app with the project virtualenv, always.
#
# `streamlit run app/streamlit_app.py` picks up whatever `streamlit` is first on
# PATH, which is frequently not this project's venv. The page then loads and
# crashes on a missing import. This script removes that footgun.
set -euo pipefail

cd "$(dirname "$0")"

VENV_PY=".venv/bin/python"
VENV_STREAMLIT=".venv/bin/streamlit"

if [[ ! -x "$VENV_STREAMLIT" ]]; then
  echo "Virtualenv not found at .venv/"
  echo
  echo "Create it and install dependencies:"
  echo "    python3 -m venv .venv"
  echo "    .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "No .env found. Copy the template and add your key:"
  echo "    cp .env.example .env"
  echo
fi

PORT="${1:-8501}"

# headless stops Streamlit from auto-launching the system browser. On macOS that
# auto-launch is what triggers a Brave permission dialog with no way to accept
# it, even though the app is running fine. We print the URL instead.
echo "Starting Streamlit on http://localhost:${PORT}"
echo "Open that URL in your browser. Ctrl-C to stop."
echo

exec "$VENV_STREAMLIT" run app/streamlit_app.py \
  --server.port "$PORT" \
  --server.headless true \
  --browser.gatherUsageStats false \
  "${@:2}"
