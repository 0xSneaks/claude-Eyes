"""Parsing and rendering. Pure functions, no I/O, so they are easy to test."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SESSION_ALERT = 80.0
WEEKLY_ALERT = 70.0
STALE_SECONDS = 3 * 3600

# Lines on the Claude Code screen that look like a usage / run-out warning.
WARNING_RE = re.compile(
    r"run out|running out|usage limit|limit reached|hit your limit|"
    r"out of (extra )?usage|approaching .*limit|\d+% of your (session|weekly|5-hour|7-day)",
    re.IGNORECASE,
)


@dataclass
class Window:
    used_pct: float | None = None
    resets_at: int | None = None  # unix epoch seconds


@dataclass
class Snapshot:
    session: Window = field(default_factory=Window)
    weekly: Window = field(default_factory=Window)
    captured_at: int | None = None  # unix epoch seconds
    warnings: list[str] = field(default_factory=list)


def _window(raw) -> Window:
    if not isinstance(raw, dict):
        return Window()
    pct = raw.get("used_percentage")
    reset = raw.get("resets_at")
    pct = float(pct) if isinstance(pct, (int, float)) and not isinstance(pct, bool) else None
    reset = int(reset) if isinstance(reset, (int, float)) and not isinstance(reset, bool) else None
    return Window(pct, reset)


def parse_rate_limits(statusline: dict) -> tuple[Window, Window]:
    """Pull (session, weekly) from Claude Code's statusline JSON.

    Documented fields: rate_limits.five_hour / rate_limits.seven_day, each with
    used_percentage (0-100) and resets_at (epoch s). Any part may be absent.
    """
    rl = statusline.get("rate_limits") if isinstance(statusline, dict) else None
    if not isinstance(rl, dict):
        return Window(), Window()
    return _window(rl.get("five_hour")), _window(rl.get("seven_day"))


def find_warnings(screen_text: str) -> list[str]:
    """Return screen lines that look like usage warnings, verbatim (trimmed), deduped."""
    out: list[str] = []
    for line in screen_text.splitlines():
        s = line.strip()
        if s and WARNING_RE.search(s) and s not in out:
            out.append(s)
    return out


def fmt_et(epoch: int | None) -> str:
    if epoch is None:
        return "UNKNOWN"
    return datetime.fromtimestamp(epoch, ET).strftime("%a %Y-%m-%d %H:%M ET")


def fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "UNKNOWN"
    return f"{pct:g}%"


def alerts(snap: Snapshot) -> list[str]:
    reasons = []
    if snap.session.used_pct is not None and snap.session.used_pct >= SESSION_ALERT:
        reasons.append(f"session {fmt_pct(snap.session.used_pct)} ≥ {SESSION_ALERT:g}%")
    if snap.weekly.used_pct is not None and snap.weekly.used_pct >= WEEKLY_ALERT:
        reasons.append(f"weekly {fmt_pct(snap.weekly.used_pct)} ≥ {WEEKLY_ALERT:g}%")
    if snap.warnings:
        reasons.append("run-out warning shown")
    return reasons


def render(snap: Snapshot, now: int, where: str) -> str:
    """Markdown for ai-coms usage/claude.md (replaces the whole file)."""
    lines = []
    reasons = alerts(snap)
    if reasons:
        lines.append(
            "**ALERT — Claude usage high (" + "; ".join(reasons) + "). "
            "New chat on Sonnet, from the latest coms handoff.**"
        )
        lines.append("")
    lines.append("# Claude usage — written by claude-eyes, read by every Claude session at check-in")
    lines.append("")
    lines.append(f"_Last checked:_ {fmt_et(now)} · _source:_ Claude Code statusline `rate_limits` on {where}")
    if snap.captured_at is None:
        lines.append("_Data captured:_ UNKNOWN")
    else:
        age = now - snap.captured_at
        stale = " · **STALE — older than 3h**" if age > STALE_SECONDS else ""
        lines.append(f"_Data captured:_ {fmt_et(snap.captured_at)}{stale}")
    lines.append("")
    lines.append("| Meter | Used | Resets |")
    lines.append("|---|---|---|")
    lines.append(f"| Current session (5-hour) | {fmt_pct(snap.session.used_pct)} | {fmt_et(snap.session.resets_at)} |")
    lines.append(f"| Weekly — all models (7-day) | {fmt_pct(snap.weekly.used_pct)} | {fmt_et(snap.weekly.resets_at)} |")
    if snap.warnings:
        lines.append("")
        lines.append("Run-out warning (word for word):")
        for w in snap.warnings:
            lines.append(f"> {w}")
    else:
        lines.append("")
        lines.append("Run-out warning: none shown on the Claude Code screen. "
                     "(Statusline data has no warning field; claude.ai's own pace text is not read.)")
    lines.append("")
    lines.append(f"Alert thresholds: session ≥ {SESSION_ALERT:g}% · weekly ≥ {WEEKLY_ALERT:g}% · any run-out warning. "
                 "Repo: 0xSneaks/claude-eyes.")
    return "\n".join(lines) + "\n"
