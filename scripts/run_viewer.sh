#!/bin/sh
# Serves the NCC viewer at http://localhost:8000
cd "$(dirname "$0")/../ncc/viewer" || exit 1
echo "Open http://localhost:8000 in your browser"
python3 -m http.server 8000
