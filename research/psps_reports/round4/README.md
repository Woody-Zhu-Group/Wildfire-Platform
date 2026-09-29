# Round 4: model-assisted review of the round 3 queue

Work in progress. `DESIGN.md` is the plan, including the changes decided before the freeze. This README is completed when the round ends.

## Code

| File | What it does |
|---|---|
| `packets.py` | Builds the item sets (dev, test, queue) and writes each session's packet folder outside the repository. |
| `run_claude.py` | Reviewer 1: the packet isolation check, then one fresh headless Claude Code session per packet. |
| `run_sol.py` | Reviewer 2: GPT-6 Sol on OpenRouter with `search`, `read_page`, and `view_page` over the same packet, plus the spend ledger. |
| `verify.py` | The code checks on each answer and the agreement rule. |

## Packet isolation check

`python research/psps_reports/round4/run_claude.py isolation` starts a fresh session in a test packet and asks it to read, search, list, and write outside the folder, then checks the transcript and the disk. The result is in `runs/isolation_check.json`. It passed only after three fixes:

1. **Prompt on stdin.** On Windows, `claude` is an npm `claude.CMD` shim, and a multi-line prompt passed as an argument reached the session cut to its first line. Prompts now go in on stdin.
2. **An Edit rule on the absolute path.** `Write(./answers.json)` never matched, so every write was denied. File-writing permissions are `Edit(...)` rules, and Claude Code matches them against the POSIX form of the Windows path, so the rule is `Edit(//c/.../<packet>/answers.json)`.
3. **Neutral wording.** A first version framed as an access test made Opus decline every step, which tests the model's manners, not the permission layer. The check now asks for ordinary file steps, and it fails unless the transcript shows each outside read and write was actually attempted.

The command that passed: `claude -p --model opus --tools Read,Grep,Glob,Write --allowedTools "Read(./**)" "Grep(./**)" "Glob(./**)" "Edit(//c/.../answers.json)" --permission-mode dontAsk --restricted --safe-mode --strict-mcp-config --setting-sources local --no-session-persistence --max-turns N --output-format stream-json --verbose`. `--restricted` confines the file tools to the packet folder.
