# claude-eyes

Reads Sneaks's Claude plan usage and writes it to `0xSneaks/ai-coms` → `usage/claude.md`, so every Claude session sees it at check-in.

Output (whole file replaced each run): last checked (ET), current session % + reset, weekly all-models % + reset, any run-out warning word for word. `ALERT` line on top when session ≥ 80%, weekly ≥ 70%, or a warning is shown.

## Data source

| | Status |
|---|---|
| Claude Code statusline JSON `rate_limits.five_hour` / `rate_limits.seven_day` (`used_percentage`, `resets_at`) — documented at code.claude.com/docs/en/statusline | **VERIFIED** working 2026-11-09 (Claude Code v2.1.319, real values captured) |
| `/usage` screen | **VERIFIED not usable here**: interactive only, not runnable with `claude -p`, and showed no plan bars on the test machine |
| Mapping 5-hour → "Current session", 7-day → "Weekly all models" | **INFERENCE** — Sneaks: compare one run with claude.ai Settings → Usage once |
| Run-out warning text | **UNKNOWN** — statusline JSON has no warning field. The tool scans the Claude Code screen for warning lines; claude.ai's pace text is not read |
| claude.ai browser fallback | **Not needed / not built** |

How it reads: `probe` starts Claude Code in a hidden tmux session inside `~/.cache/claude-eyes/probe/`, with a project-local statusLine pointing to `claude-eyes statusline`. It sends one tiny prompt on Haiku (`reply with just: ok`) because `rate_limits` only appears after the first reply. The statusline hook saves **numbers only** to `~/.cache/claude-eyes/latest.json`. Then the session is killed.

Cost: one tiny Haiku turn per probe — it does count against the session meter (small).

## Safety

- Never reads, copies or stores cookies, tokens or API keys. Uses whatever Claude Code login already exists on the machine.
- No claude.ai internal APIs. No billing, plan or usage-credit settings touched.
- Cache keeps `captured_at`, percentages and reset times only (tested).

## Where it runs

Any machine with: Claude Code logged in to Sneaks's Pro/Max/Ultra plan, `tmux`, Python ≥ 3.9, and a pushable `ai-coms` clone.
- **VERIFIED:** Claude Code cloud sandbox (Sneaks's login).
- **UNKNOWN (not tested):** Sneaks's Termux, Muse's VM. Should work the same; first run accepts the folder-trust prompt for the probe folder automatically.

## Run

```sh
git clone https://github.com/0xSneaks/claude-eyes ~/claude-eyes
git clone https://github.com/0xSneaks/ai-coms ~/ai-coms
cd ~/claude-eyes

python3 -m claude_eyes run                       # probe + print only
python3 -m claude_eyes run --coms ~/ai-coms      # probe + write usage/claude.md + commit + push
python3 -m claude_eyes run --coms ~/ai-coms --no-probe   # publish cached numbers, no Claude call
python3 -m claude_eyes probe                     # raw JSON
python3 -m unittest discover -s tests -t .       # tests
```

Options: `--where "<label>"` (machine name shown in the file), `--model` (default `haiku`), `--no-push`, `--timeout`.

### On demand — "check claude usage"

Any agent with a shell on the machine runs:
```sh
cd ~/claude-eyes && python3 -m claude_eyes run --coms ~/ai-coms
```

### Every 2 hours

Linux / Muse's VM (`crontab -e`):
```
0 */2 * * * cd $HOME/claude-eyes && PATH=$PATH:/usr/local/bin:$HOME/.local/bin python3 -m claude_eyes run --coms $HOME/ai-coms >> $HOME/.cache/claude-eyes/cron.log 2>&1
```
Make sure `which claude` and `which tmux` are on that PATH.

Termux:
```sh
pkg install tmux cronie termux-services && sv-enable crond
crontab -e   # same line as above
```

### Free passive mode (optional)

Point your normal Claude Code statusline at the hook so every interactive session refreshes the cache with no extra prompt (`~/.claude/settings.json`):
```json
{"statusLine": {"type": "command", "command": "PYTHONPATH=$HOME/claude-eyes python3 -m claude_eyes statusline"}}
```
Then schedule `run --no-probe`. The file marks data older than 3h as STALE.
