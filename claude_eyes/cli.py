"""claude-eyes CLI.

  statusline  Claude Code statusLine command: caches rate_limits numbers only.
  probe       Start Claude Code headless in tmux, send one tiny Haiku prompt,
              wait for fresh rate_limits, scan the screen for warnings.
  run         probe (unless --no-probe) -> render -> write ai-coms usage/claude.md -> commit + push.

Never reads, copies or stores credentials. The cache holds percentages and reset times only.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import core

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "claude-eyes"
CACHE = CACHE_DIR / "latest.json"
PROBE_DIR = CACHE_DIR / "probe"
TMUX_SESSION = "claude-eyes-probe"
PROBE_PROMPT = "reply with just: ok"


# ---------- cache ----------

def save_cache(statusline: dict, path: Path = CACHE) -> bool:
    """Store only the numbers we need. Returns True if rate_limits were present."""
    session, weekly = core.parse_rate_limits(statusline)
    if session.used_pct is None and weekly.used_pct is None:
        return False
    data = {
        "captured_at": int(time.time()),
        "five_hour": {"used_percentage": session.used_pct, "resets_at": session.resets_at},
        "seven_day": {"used_percentage": weekly.used_pct, "resets_at": weekly.resets_at},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)
    return True


def load_cache(path: Path = CACHE) -> core.Snapshot:
    if not path.exists():
        return core.Snapshot()
    d = json.loads(path.read_text())
    session, weekly = core.parse_rate_limits({"rate_limits": d})
    return core.Snapshot(session=session, weekly=weekly, captured_at=d.get("captured_at"))


# ---------- statusline ----------

def cmd_statusline(args) -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    save_cache(payload, Path(args.cache))
    s, w = core.parse_rate_limits(payload)
    print(f"5h {core.fmt_pct(s.used_pct)} · 7d {core.fmt_pct(w.used_pct)}")
    return 0


# ---------- probe ----------

def _tmux(*a, check=True) -> str:
    return subprocess.run(["tmux", *a], check=check, capture_output=True, text=True).stdout


def _screen(history: bool = False) -> str:
    extra = ["-S", "-200"] if history else []
    return _tmux("capture-pane", "-t", TMUX_SESSION, "-p", *extra, check=False)


def probe(timeout: int = 120, model: str = "haiku") -> core.Snapshot:
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    (PROBE_DIR / ".claude").mkdir(exist_ok=True)
    sl_cmd = (f"PYTHONPATH={shlex.quote(str(ROOT))} {shlex.quote(sys.executable)} "
              f"-m claude_eyes statusline --cache {shlex.quote(str(CACHE))}")
    (PROBE_DIR / ".claude" / "settings.local.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": sl_cmd}}))

    before = load_cache().captured_at or 0
    _tmux("kill-session", "-t", TMUX_SESSION, check=False)
    _tmux("new-session", "-d", "-s", TMUX_SESSION, "-x", "160", "-y", "60", "-c", str(PROBE_DIR),
          f"claude --model {shlex.quote(model)}")
    try:
        # Wait for the prompt box; accept folder trust for our own probe dir on first run.
        deadline = time.time() + 45
        while time.time() < deadline:
            time.sleep(2)
            scr = _screen()
            if "Yes, I trust this folder" in scr:
                _tmux("send-keys", "-t", TMUX_SESSION, "Down")
                time.sleep(0.5)
                _tmux("send-keys", "-t", TMUX_SESSION, "Enter")
                continue
            if "❯" in scr and "trust" not in scr.lower():
                break
        _tmux("send-keys", "-t", TMUX_SESSION, "-l", PROBE_PROMPT)
        time.sleep(0.5)
        _tmux("send-keys", "-t", TMUX_SESSION, "Enter")

        deadline = time.time() + timeout
        snap = core.Snapshot()
        while time.time() < deadline:
            time.sleep(3)
            snap = load_cache()
            if (snap.captured_at or 0) > before:
                time.sleep(3)  # let the screen settle so warnings render
                break
        else:
            raise RuntimeError("no fresh rate_limits from Claude Code within timeout "
                               "(not logged in to a Pro/Max/Ultra plan, or no reply)")
        snap = load_cache()
        snap.warnings = core.find_warnings(_screen(history=True))
        return snap
    finally:
        _tmux("kill-session", "-t", TMUX_SESSION, check=False)


def cmd_probe(args) -> int:
    snap = probe(args.timeout, args.model)
    print(json.dumps({
        "captured_at": snap.captured_at,
        "session": vars(snap.session), "weekly": vars(snap.weekly), "warnings": snap.warnings,
    }, indent=2))
    return 0


# ---------- run / publish ----------

def _git(repo: Path, *a) -> None:
    subprocess.run(["git", "-C", str(repo), *a], check=True)


def cmd_run(args) -> int:
    snap = load_cache() if args.no_probe else probe(args.timeout, args.model)
    md = core.render(snap, int(time.time()), args.where)
    if not args.coms:
        sys.stdout.write(md)
        return 0
    repo = Path(args.coms)
    target = repo / "usage" / "claude.md"
    if not args.no_push:
        _git(repo, "pull", "--rebase", "--quiet")
    target.parent.mkdir(exist_ok=True)
    target.write_text(md)
    sys.stdout.write(md)
    if args.no_push:
        return 0
    _git(repo, "add", "usage/claude.md")
    if subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--quiet"]).returncode == 0:
        return 0
    _git(repo, "commit", "--quiet", "-m", f"usage: claude-eyes check {core.fmt_et(int(time.time()))}")
    for i in range(4):
        if subprocess.run(["git", "-C", str(repo), "push", "--quiet"]).returncode == 0:
            return 0
        time.sleep(2 ** (i + 1))
        _git(repo, "pull", "--rebase", "--quiet")
    print("push failed", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="claude-eyes")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("statusline")
    s.add_argument("--cache", default=str(CACHE))
    s.set_defaults(fn=cmd_statusline)

    for name, fn in (("probe", cmd_probe), ("run", cmd_run)):
        q = sub.add_parser(name)
        q.add_argument("--timeout", type=int, default=120)
        q.add_argument("--model", default="haiku", help="cheapest model for the one probe reply")
        q.set_defaults(fn=fn)
        if name == "run":
            q.add_argument("--coms", help="path to an ai-coms clone; omit to print only")
            q.add_argument("--no-probe", action="store_true", help="use the cached numbers, no Claude call")
            q.add_argument("--no-push", action="store_true")
            q.add_argument("--where", default=socket.gethostname(), help="machine label shown in the file")

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
