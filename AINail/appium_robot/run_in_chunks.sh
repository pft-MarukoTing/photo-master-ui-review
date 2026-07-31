#!/bin/bash
# Run beauty_cam.robot in fixed-size chunks, restarting WDA + Appium between
# each chunk. WDA is a long-lived process on the phone (normally started once
# by hand via Xcode Product > Test and left running); Appium detects it on
# port 8100 and reuses it rather than rebuilding, so restarting only Appium
# does NOT reset WDA's own memory. This script replaces the manual
# "open Xcode, Product > Test" step with an equivalent `xcodebuild test`
# invocation so WDA itself gets torn down and relaunched fresh every chunk.
#
# Usage: ./run_in_chunks.sh [--source-offset N] [--reference-offset N]
#                            [--start N] [--end N] [--chunk-size N]
#   --source-offset     base offset for the source photo (default 0)
#   --reference-offset  base offset for the reference/custom photo (default 200)
#   --start             iteration to (re)start from - use this to resume after
#                        an interruption instead of redoing finished work (default 0)
#   --end               exclusive upper bound to run up to, i.e. iterations run
#                        are [start, end) (default 500)
#   --chunk-size        iterations per WDA/Appium session (default 50)
#
# If the run is interrupted partway (Ctrl+C, crash, phone locked, etc), note
# the last "=== Chunk N: iterations X..Y ===" line that completed and re-run
# with --start set to Y+1 to continue instead of redoing finished work.
#
# Example:
#   ./run_in_chunks.sh \
#       --source-offset 0 \
#       --reference-offset 200 \
#       --start 0 \
#       --end 500 \
#       --chunk-size 50
#
# Prerequisite: iPhone connected to this Mac via USB (same as manual flow),
# and the screen UNLOCKED - `xcodebuild test` blocks indefinitely waiting for
# unlock if the screen is locked (it cannot unlock the device itself), so set
# Settings > Display & Brightness > Auto-Lock to Never before a long run.

set -euo pipefail

SOURCE_OFFSET=0
REFERENCE_OFFSET=200
START_ITERATION=0
END_ITERATION=500
CHUNK=50

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source-offset) SOURCE_OFFSET="$2"; shift 2 ;;
        --reference-offset) REFERENCE_OFFSET="$2"; shift 2 ;;
        --start) START_ITERATION="$2"; shift 2 ;;
        --end) END_ITERATION="$2"; shift 2 ;;
        --chunk-size) CHUNK="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROBOT_FILE="${SCRIPT_DIR}/beauty_cam.robot"

WDA_PROJECT="/Users/jackcylin/WebDriverAgent-15.1.3/WebDriverAgent.xcodeproj"
WDA_SCHEME="WebDriverAgentRunner"
UDID="$(grep -m1 '\${UDID}' "${SCRIPT_DIR}/local_config.robot" | awk '{print $2}')"

APPIUM_PORT=4723
CONDA_ENV="appium"

LOG_DIR="/tmp/nail_test_chunks"
mkdir -p "$LOG_DIR"

if [[ -z "$UDID" ]]; then
    echo "Could not read UDID from local_config.robot" >&2
    exit 1
fi

start_wda() {
    local log_file="$1"
    # xcodebuild doesn't need the conda env, and piping its stdout straight to
    # a file leaves it fully block-buffered (nothing shows up until the
    # buffer fills or the process exits). Route it through `script` to give
    # it a pty, which makes it flush line-by-line like a real terminal would.
    script -q "$log_file" xcodebuild test \
        -project "$WDA_PROJECT" \
        -scheme "$WDA_SCHEME" \
        -destination "id=${UDID}" \
        -allowProvisioningUpdates \
        < /dev/null &
    WDA_PID=$!
    echo "Started WDA build/test (pid=$WDA_PID), waiting for it to be ready (make sure the phone is unlocked)..."
    # 127.0.0.1:8100 is only reachable once Appium sets up its own USB
    # port forward during session creation - it's not up yet at this point.
    # WDA itself has started once its embedded server prints its own URL.
    for _ in $(seq 1 300); do
        if grep -q "ServerURLHere->" "$log_file" 2>/dev/null; then
            return 0
        fi
        if ! kill -0 "$WDA_PID" 2>/dev/null; then
            echo "WDA process exited before becoming ready, see $log_file" >&2
            exit 1
        fi
        sleep 1
    done
    echo "WDA did not become ready in time, see $log_file" >&2
    exit 1
}

stop_wda() {
    if [[ -n "${WDA_PID:-}" ]]; then
        kill "$WDA_PID" 2>/dev/null || true
        wait "$WDA_PID" 2>/dev/null || true
        WDA_PID=""
    fi
    # xcodebuild spawns the actual xcodebuild/xctest helper as children; make
    # sure nothing is left holding the WDA port before the next chunk starts.
    pkill -f "WebDriverAgentRunner" 2>/dev/null || true
}

start_appium() {
    local log_file="$1"
    conda run -n "$CONDA_ENV" appium --port "$APPIUM_PORT" --log "$log_file" &
    APPIUM_PID=$!
    echo "Started Appium (pid=$APPIUM_PID), waiting for it to be ready..."
    for _ in $(seq 1 60); do
        if curl -s "http://127.0.0.1:${APPIUM_PORT}/status" > /dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "Appium did not become ready in time" >&2
    exit 1
}

stop_appium() {
    if [[ -n "${APPIUM_PID:-}" ]]; then
        kill "$APPIUM_PID" 2>/dev/null || true
        wait "$APPIUM_PID" 2>/dev/null || true
        APPIUM_PID=""
    fi
}

cleanup() {
    stop_appium
    stop_wda
}
trap cleanup EXIT

start=$START_ITERATION
chunk_num=1
while (( start < END_ITERATION )); do
    end=$((start + CHUNK))
    if (( end > END_ITERATION )); then
        end=$END_ITERATION
    fi

    wda_log="${LOG_DIR}/wda_chunk_${chunk_num}.log"
    appium_log="${LOG_DIR}/appium_chunk_${chunk_num}.log"

    echo "=== Chunk ${chunk_num}: iterations ${start}..$((end - 1)) ==="
    start_wda "$wda_log"
    start_appium "$appium_log"

    # Filter the console-only noise from AppiumLibrary's deprecation warning
    # for "Element Should Be Visible" (still fully logged in log.html/output.xml).
    # set +e/-e brackets the pipeline so `set -e`/pipefail can't act on its
    # collective exit status (which would reflect grep, not robot) - grep -v can
    # legitimately exit 1 if it filters out every line, which would otherwise be
    # misread as robot having failed. PIPESTATUS[0] (robot's real exit code) is
    # captured unconditionally and checked explicitly instead.
    set +e
    conda run -n "$CONDA_ENV" robot \
        --variable "START_ITERATION:${start}" \
        --variable "END_ITERATION:${end}" \
        --variable "SOURCE_PHOTO_START_OFFSET:${SOURCE_OFFSET}" \
        --variable "REFERENCE_PHOTO_START_OFFSET:${REFERENCE_OFFSET}" \
        "$ROBOT_FILE" \
        | grep -v -E "is deprecated\. Use \`Expect Element\` instead|^Verifies that the element identified by \`\`locator\`\` is visible\.$"
    robot_exit=${PIPESTATUS[0]}
    set -e
    if [[ $robot_exit -ne 0 ]]; then
        echo "Chunk ${chunk_num} failed (robot exit ${robot_exit}), see $wda_log / $appium_log if still present" >&2
        exit "$robot_exit"
    fi

    stop_appium
    stop_wda

    # Chunk succeeded (the `if` above would have already exited the script on
    # failure) - the logs are only useful for debugging a crash, so drop them
    # now instead of letting them pile up on disk across many chunks.
    rm -f "$wda_log" "$appium_log"

    sleep 3

    start=$end
    chunk_num=$((chunk_num + 1))
done

echo "All iterations up to ${END_ITERATION} complete."
