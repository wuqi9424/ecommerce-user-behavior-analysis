#!/usr/bin/env bash
# Local portfolio launcher; credentials must already be in the environment.
set -euo pipefail
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$project_root/.venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
  echo "Missing project .venv. Create it and install requirements.txt and requirements-ui.txt." >&2
  exit 1
fi
if [[ -z "${OPENAI_API_KEY:-}" || -z "${OPENAI_MODEL:-}" ]]; then
  echo "Set OPENAI_API_KEY and OPENAI_MODEL in the current environment before starting." >&2
  exit 1
fi
if [[ -z "${SSL_CERT_FILE:-}" ]]; then
  SSL_CERT_FILE="$("$python_bin" - <<'PYTHON'
from pathlib import Path
import ssl
paths = ssl.get_default_verify_paths()
if paths.cafile and Path(paths.cafile).is_file():
    print(paths.cafile)
else:
    import certifi
    print(certifi.where())
PYTHON
)"
  export SSL_CERT_FILE
fi
if [[ ! -r "$SSL_CERT_FILE" ]]; then
  echo "SSL_CERT_FILE must point to a readable CA certificate bundle." >&2
  exit 1
fi
cd "$project_root"
exec "$python_bin" -m streamlit run ai_agent/streamlit_app.py --server.address 127.0.0.1 "$@"
