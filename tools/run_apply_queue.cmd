@echo off
REM Unattended apply-queue run. Double-click it, or point Windows Task Scheduler at this file.
REM Fills ready applications; parks anything that needs a person (captchas, consents, and the final
REM Submit click unless the candidate opted in to auto-submit in facts.json automation.auto_submit).
REM When at the keyboard, use:  python tools\apply_queue.py run --present
cd /d "%~dp0.."
python tools\apply_queue.py run --until-done
