#!/bin/bash
# Render one subtitle with the package's subtitle renderer, the helper the
# preview starts (--subtitle-render-worker): proof that QtWebEngine and its
# Chromium are in the package and run, sandboxed or (where the sandbox
# cannot start) not.
#
#   smoke-render.sh <style.html> <command> [args...]
#
# <command> is the package's Subtitld; <style.html>, Classic's template in it.
# Passes if a layer of the subtitle comes back within 60 seconds.
set -u

template=$1
shift
log=${SMOKE_RENDER_LOG:-smoke-render.log}

job='{"cmd": "render", "id": 1, "size": [640, 360], "urgent": true, "page": {"html": "Smoke test", "align": 2,
  "vars": {"--font-family": "\"Montserrat\"", "--font-size": "60px", "--color": "#ffffff",
           "--shadow-x": "2px", "--shadow-y": "2px", "--shadow-color": "#000000",
           "--backgroundbox-padding": "10px", "--backgroundbox-border-radius": "5px",
           "--backgroundbox-color": "rgba(0, 0, 0, 0.333)"},
  "attrs": {"data-shadow-enabled": "1", "data-backgroundbox-enabled": "1"},
  "aspect": 1.7778, "safe": [0.1, 0.1], "offset": 0, "duration": 1000, "pad": 0.03}}'
job=$(echo "$job" | tr -d '\n')

for attempt in 1 2; do
    # Where Chromium's sandbox cannot start, the helper stops before a page
    # is ready; Subtitld then starts it again without it, and so does this.
    { printf '{"cmd": "template", "path": "%s"}\n%s\n' "$template" "$job"; sleep 60; } \
        | timeout 60 "$@" --subtitle-render-worker > "$log.out" 2> "$log" &
    pid=$!
    for ((i = 1; i <= 60; i++)); do
        sleep 1
        if grep -aq '"id": 1, "rect": \[' "$log.out" 2>/dev/null; then
            kill "$pid" 2>/dev/null
            grep -a '"id": 1' "$log.out" | head -1 | cut -c1-120
            echo "rendered a subtitle after ${i}s"
            exit 0
        fi
        kill -0 "$pid" 2>/dev/null || break
    done
    wait "$pid"
    code=$?
    if [ "$attempt" = 1 ] && ! grep -aq '"ready": true' "$log.out"; then
        echo "Chromium's sandbox cannot start here: again without it"
        export QTWEBENGINE_DISABLE_SANDBOX=1
        continue
    fi
    tail -30 "$log"
    echo "::error::the subtitle renderer gave no layer (exit $code)"
    exit 1
done
