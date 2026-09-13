#!/bin/sh
set -eu

entrypoint="${ENTRYPOINT:-main.py}"
input_file="/workspace/.polyworkspace-stdin"

if [ ! -f "$entrypoint" ]; then
    found_path=$(find . -name "$(basename "$entrypoint")" -type f | head -n 1)
    if [ -n "$found_path" ]; then
        entrypoint="$found_path"
    else
        echo "Python entrypoint not found: $entrypoint" >&2
        exit 1
    fi
fi

# Only redirect stdin if the input file actually has content
if [ -s "$input_file" ]; then
    exec python -B "$entrypoint" < "$input_file"
fi

exec python -B "$entrypoint"