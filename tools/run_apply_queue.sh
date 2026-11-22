#!/usr/bin/env bash
# Linux/macOS twin of run_apply_queue.cmd. Unattended apply-queue run: fills applications
# up to the review page and parks anything needing a person. Unless the candidate has opted in to
# auto-submit (facts.json automation.auto_submit), every item stops at the review page for a
# person to click Submit. NEEDS_HUMAN lines on stdout are what a watching Claude session relays
# to the user. When you are at the browser window, use:
#   python tools/apply_queue.py run --present
cd "$(dirname "$0")/.." || exit 1
exec python tools/apply_queue.py run --until-done "$@"
