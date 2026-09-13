#!/bin/sh
set -eu

main_class="${JAVA_MAIN_CLASS:-Main}"
input_file="/workspace/.polyworkspace-stdin"

if ! find . -type f -name "*.java" | grep -q .; then
    echo "No Java source files were found." >&2
    exit 1
fi

find . -type f -name "*.java" -print0 \
    | xargs -0 javac -d /tmp/polyworkspace-java-classes

# Only redirect stdin if the input file actually has content
if [ -s "$input_file" ]; then
    exec java \
        -cp /tmp/polyworkspace-java-classes \
        "$main_class" < "$input_file"
fi

exec java \
    -cp /tmp/polyworkspace-java-classes \
    "$main_class"