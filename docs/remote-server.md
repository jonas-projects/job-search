# Optional: running the browser on a headless machine

The default setup needs none of this. On a laptop or desktop, Chrome opens as a normal window on
your own screen, and you clear captchas and sign in there.

This page is for the less common case: the repo lives on an always-on Linux machine (a home
server, WSL, a VPS) that has no screen, and you drive Claude from another device.

## What the tools expect

| Piece | How the tools find it | Notes |
|---|---|---|
| A virtual display | `DISPLAY`, or an Xvfb on `:99` (picked up automatically when `DISPLAY` is unset) | Chrome still runs headed; Indeed and ZipRecruiter block headless sign-in. |
| A way to see and click that display | `INDEED_VIEWER_URL`, or the first line of `~/.config/jobsearch/viewer_url` | For example noVNC in front of x11vnc. The link is appended to every alert. |
| Phone alerts (optional) | `INDEED_NTFY_TOPIC`, or the first line of `~/.config/jobsearch/ntfy_topic` | A push goes to `https://ntfy.sh/<topic>` whenever a person is needed. The topic name is the only secret. |

Keep the viewer link, any VNC password and the ntfy topic **outside the repo**. Put the viewer
behind a private network (a VPN such as Tailscale, or an SSH tunnel) and a password; anyone who can
reach it controls a browser signed in to your job accounts.

## Alerts

Anything that needs a person prints one line on stderr, at most every 5 minutes per reason
(`INDEED_ALERT_REPEAT`, seconds):

```
NEEDS_HUMAN: <reason> | <detail> | <viewer link, if set>
```

`tools/apply_queue.py` forwards those lines to its stdout with the application folder appended.
When Claude runs a login or a queue in the background, it should watch for `NEEDS_HUMAN` lines and
pass each one on to you, with the viewer link, in the chat.

Captcha alerts fire only after a challenge has stayed up for 15 seconds
(`INDEED_CAPTCHA_ALERT_AFTER`), because many clear on their own. `INDEED_CAPTCHA_SOUND=0` silences
alerts entirely.

## Notes

- `tools/run_apply_queue.sh` is the Linux/macOS twin of `tools/run_apply_queue.cmd`.
- A signed-in Chrome profile cannot be copied between machines (its cookies are locked to the
  account that created it). Sign in again on each machine with `/indeed_login`.
- To stop a run mid-application, send SIGTERM to the `indeed_apply.py` child only. The runner
  records the item as parked, closes the shared Chrome and releases the lock; nothing is
  submitted. Then `withdraw` or `retry` it.
