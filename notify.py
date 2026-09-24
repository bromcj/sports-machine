"""Get a monitor finding in front of you, without sending your data anywhere.

Three channels, in order of how hard they are to miss:

  ALERTS.md        always written, at the repo root. Cleared to a one-line
                   all-clear when nothing is wrong, so a stale file can never
                   masquerade as a current problem.
  desktop toast    best effort, Windows only, no dependency and no account -
                   it uses the NotifyIcon balloon that ships with .NET.
  ntfy.sh          OFF unless you set SPORTS_MACHINE_NTFY_TOPIC.

WHY NTFY IS OFF BY DEFAULT.

The first two keep everything on this machine. ntfy sends the text to a
third-party server, and a free ntfy topic is readable by ANYONE who guesses or
learns the topic name - there is no password. The findings are not secrets
("3 games unsettled"), but a topic name is not access control, and turning on
outbound publishing is your decision rather than a default I pick for you.

If you want it, choose something long and unguessable and set it:

    setx SPORTS_MACHINE_NTFY_TOPIC "sportsmachine-<something-random>"

then install the ntfy app on your phone and subscribe to that topic. No
account, no credential, and nothing in this repo ever stores it - it is read
from the environment. To stop, clear the variable.

Gmail was the other option and I did not build it: it needs an app password
living on this machine, and handling a credential is not something I will set
up for you. If you would rather have email, create the app password yourself,
put it in an env var, and say so - the backend is a dozen lines.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
ALERTS = ROOT / "ALERTS.md"
NTFY_ENV = "SPORTS_MACHINE_NTFY_TOPIC"
NTFY_HOST = "https://ntfy.sh"


def _write_alerts_file(findings, stamp: str) -> None:
    """Always. A file you will trip over beats a channel you might not see."""
    bad = [f for f in findings if f["level"] in ("CRITICAL", "ERROR")]
    warn = [f for f in findings if f["level"] == "WARNING"]
    # Readable, not an ISO timestamp. This file is for a person.
    try:
        import datetime as _dt
        pretty = (_dt.datetime.fromisoformat(stamp).astimezone()
                  .strftime("%a %d %b, %I:%M %p").replace(" 0", " "))
    except Exception:
        pretty = stamp
    lines = [f"# Alerts — {pretty}", ""]
    if not bad and not warn:
        lines += ["**All clear.** Nothing needs your attention.", "",
                  f"_{len(findings)} checks ran._"]
    else:
        if bad:
            lines += [f"## Needs attention ({len(bad)})", ""]
            lines += [f"- **{f['check']}** — {f['detail']}" for f in bad] + [""]
        if warn:
            lines += [f"## Worth a look ({len(warn)})", ""]
            lines += [f"- {f['check']} — {f['detail']}" for f in warn] + [""]
        lines += ["---", "",
                  "Run `python monitor.py` to re-check, or "
                  "`python monitor.py --tail 40` for history.",
                  "This file is rewritten every run, so it is never stale."]
    ALERTS.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _toast(title: str, body: str) -> bool:
    """Windows balloon notification. No dependency, no account, best effort."""
    if sys.platform != "win32":
        return False
    # Single-quoted PowerShell strings, with embedded quotes doubled, so a
    # game name with an apostrophe cannot break the script.
    t = title.replace("'", "''")
    b = body.replace("'", "''")[:250]
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$n = New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon = [System.Drawing.SystemIcons]::Warning;"
        f"$n.BalloonTipTitle = '{t}';"
        f"$n.BalloonTipText = '{b}';"
        "$n.Visible = $true;"
        "$n.ShowBalloonTip(10000);"
        "Start-Sleep -Seconds 11;"
        "$n.Dispose()")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", script],
                           capture_output=True, timeout=30)
        # Reported as delivered only if PowerShell says it worked; it used to
        # return True whatever happened.
        return r.returncode == 0
    except Exception:
        return False


def _ntfy(title: str, body: str) -> bool:
    """Opt-in only. Nothing leaves this machine unless the topic is set."""
    topic = os.environ.get(NTFY_ENV, "").strip()
    if not topic:
        return False
    try:
        import requests
        r = requests.post(f"{NTFY_HOST}/{topic}",
                          data=body.encode("utf-8"),
                          headers={"Title": title, "Priority": "high",
                                   "Tags": "warning"},
                          timeout=15)
        return r.ok
    except Exception:
        return False


def deliver(findings, stamp: str) -> dict:
    """Write the alerts file, and push the serious ones. Returns what was used."""
    _write_alerts_file(findings, stamp)
    bad = [f for f in findings if f["level"] in ("CRITICAL", "ERROR")]
    used = {"alerts_file": True, "toast": False, "ntfy": False,
            "n_serious": len(bad)}
    if not bad:
        return used
    title = f"Sports Machine: {len(bad)} thing(s) need attention"
    body = "\n".join(f"{f['check']}: {f['detail']}" for f in bad[:5])
    used["toast"] = _toast(title, body)
    used["ntfy"] = _ntfy(title, body)
    return used


if __name__ == "__main__":
    # Send a test through every enabled channel.
    demo = [{"level": "ERROR", "check": "delivery test",
             "detail": "if you can see this, alerts reach you"}]
    import datetime as dt
    got = deliver(demo, dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    print(json.dumps(got, indent=2))
    print(f"\nALERTS.md written to {ALERTS}")
    if not got["ntfy"]:
        print(f"ntfy is OFF (set {NTFY_ENV} to enable). See the module docstring.")
