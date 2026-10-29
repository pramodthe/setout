#!/bin/sh
# Rebuild the NCC 3D scenes from ncc/rules/rules.json
cd "$(dirname "$0")/../ncc/generator" || exit 1
python3 detail_generator.py "$@"
