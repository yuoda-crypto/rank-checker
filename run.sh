#!/bin/zsh
cd "$(dirname "$0")"
.venv/bin/python rank_checker.py "$@"
