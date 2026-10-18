#!/bin/bash
# Start the app and check it is still running after a while: proof that the
# package's imports, Qt plugins and libraries all load.
#
#   smoke.sh <seconds> <command> [args...]
#
# Passes if the command is alive after <seconds>, fails (with the end of its
# output) if it exits before. Works on Linux and macOS; Windows has its own
# step in build.yml.
#
# SMOKE_FAIL_ON, an extended regular expression, also fails it when the
# output matches: an app can run on without its window (Qt saying it
# "Could not create a shell surface object", say).
set -u

secs=$1
shift
log=${SMOKE_LOG:-smoke.log}

"$@" > "$log" 2>&1 &
pid=$!
for ((i = 1; i <= secs; i++)); do
    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then
        wait "$pid"
        code=$?
        tail -40 "$log"
        echo "::error::$1 exited ($code) after ${i}s instead of running"
        exit 1
    fi
done
kill "$pid" 2>/dev/null
sleep 2
kill -9 "$pid" 2>/dev/null || true
tail -15 "$log"
if [ -n "${SMOKE_FAIL_ON:-}" ] && grep -Eq "$SMOKE_FAIL_ON" "$log"; then
    grep -E "$SMOKE_FAIL_ON" "$log" | head -5
    echo "::error::$1 ran, but its output says it failed: /$SMOKE_FAIL_ON/"
    exit 1
fi
echo "still running after ${secs}s"
