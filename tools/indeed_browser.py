"""Hold one signed-in Indeed Chrome open for a whole apply-queue run.

2026-10-10: every application used to launch and quit its own Chrome. Cloudflare's
"Verify you are human" check, once the user ticks it, holds for a while in the browser, so one
long-lived Chrome per batch means the user ticks it once per batch rather than per application.

apply_queue.py starts this, waits for the DevTools port, and passes INDEED_CDP_URL to each
indeed_apply.py run, which attaches, works in its own tab and closes only that tab. Launched the
same way indeed_apply.py launches Chrome (same profile, same flags), plus the local debugging
port. It runs until terminated.

  python tools/indeed_browser.py [--port 9223]
"""
import argparse
import os
import signal
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".agents", "skills", "indeed-personal", "cli"))
import indeed_personal as ip  # noqa: E402

DEFAULT_PORT = 9223


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    a = ap.parse_args()

    stop = {"now": False}

    def _stop(*_):
        stop["now"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    ip._ARGS.append(f"--remote-debugging-port={a.port}")
    from patchright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = ip._context(p, headless=False)
        # A keeper tab, so the window survives each application closing its own tab.
        keeper = ctx.pages[0] if ctx.pages else ctx.new_page()
        print(f"READY port {a.port}", flush=True)
        while not stop["now"]:
            try:
                keeper.wait_for_timeout(1000)  # never time.sleep: keeps the driver pumping
            except Exception:  # noqa: BLE001  (window closed by hand, or Chrome died)
                break
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
