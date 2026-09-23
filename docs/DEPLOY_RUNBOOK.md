# Deploy runbook: backend EC2 host

For a person connected to the backend host (`ip-172-31-2-9`) through AWS
Systems Manager Session Manager. There is no SSH. Every command below runs as
the `ubuntu` user unless it starts with `sudo`.

Covers: the merge and deploy order, pulling `main` and restarting services,
health checks, Jev shadow mode, locking port 8004 to CloudFront, rotating the
TypeSafe key, and rollback.

Rules that apply throughout:

- Never print `.env` or `TYPESAFE_API_KEY`. Do not `cat .env`, do not
  `grep TYPESAFE .env` without masking, do not paste values into chat or
  tickets.
- Deploy only `main`. Branches are reviewed and merged on GitHub first.
- Write down the commit you started from before you change anything (step 1).
  Rollback depends on it.

## Merge and deploy order

Do these in order. Each step depends on the one before it. Merges happen on
GitHub after review; nobody merges from the host.

1. **Merge PR #22 (`router-paraphrase-fixes`) into `main`.** This is the
   router fix that stops partial and silently wrong answers. The smoke test
   checks tagged `requires PR #22` start passing only after it is deployed.
2. **Rebase `jev-shadow` onto `main`, then merge it.** After PR #22 lands:
   `git fetch platform && git rebase platform/main` on `jev-shadow`, rerun
   `pytest tests/agent`, push, and get the PR reviewed and merged. Every
   `AGENT_JEV_MODE` stays `off` by default, so merging changes no answers.
3. **Deploy `main`** (sections 1 to 4 below). This PR (#24, the smoke test,
   `shadow_report.py`, and the risk unit) must be on `main` by then too. After
   the deploy, run `SMOKE_PR22_MERGED=1 bash scripts/smoke_test.sh`; every
   check must pass, including the ones tagged `requires PR #22`.
4. **Enable shadow mode** (section 5). Its precondition check confirms the
   Jev code arrived with step 2.
5. **Run `shadow_report.py` after real traffic** (section 5.3), once the log
   holds a meaningful number of real questions (at least a day of traffic).
   Production shadow logs are the next clean test set: record the first
   report before anyone tunes routing or Jev against it, and say in any
   accuracy claim whether the rows were already used for tuning.

Do not skip ahead. Shadow mode on a commit without `jev-shadow` does nothing,
and a shadow report on a router without PR #22 measures disagreements that
are already fixed.

## 0. Get a shell as ubuntu

Start a Session Manager session to the backend instance (EC2 console, select
the instance, Connect, Session Manager tab, Connect). The session starts as
`ssm-user` in `sh`. Switch:

```bash
sudo su - ubuntu
bash
cd /home/ubuntu/Wildfire-Services
```

Services on this host (from `deploy/systemd/`):

| unit | port | health URL |
|---|---|---|
| `wildfire-data-query` | 8000 | `http://127.0.0.1:8000/health` |
| `wildfire-risk-forecasting` | 8001 | `http://127.0.0.1:8001/health` |
| `wildfire-visualization` | 8002 | `http://127.0.0.1:8002/health` |
| `wildfire-comparison` | 8003 | `http://127.0.0.1:8003/health` |
| `wildfire-agent` | 8004 | `http://127.0.0.1:8004/health` |
| `wildfire-frontend` | 8765 | `http://127.0.0.1:8765/` |
| `wildfire-gpu-control` | 8005 | legacy, keep disabled |

The agent calls the risk service at `RISK_FORECASTING_BASE_URL` (default
`http://127.0.0.1:8001`). `wildfire-risk-forecasting` is newer than the other
units, so the first deploy that includes it must install it (section 2.1).

The model host (172.31.6.133, Ollama `qwen2.5:7b`, CPU only) is separate and
is not restarted by this runbook.

## 1. Record the current state

```bash
cd /home/ubuntu/Wildfire-Services
git remote -v                      # origin must be Woody-Zhu-Group/Wildfire-Platform
git branch --show-current          # expect main
git status --short                 # expect no output
git rev-parse HEAD | tee -a /home/ubuntu/deploy_history.txt
git log --oneline -1
```

Stop if:

- `origin` is not the platform repo.
- `git status --short` shows modified tracked files. Someone edited code on the
  box. Find out what and why before pulling; do not discard it.

Untracked files that `git status` shows (logs, `.venv`) are fine only if they
are yours to keep; `.env` is gitignored and never shows.

## 2. Pull main

```bash
git fetch origin
git log --oneline HEAD..origin/main          # what is about to deploy
git diff --stat HEAD origin/main -- requirements.txt deploy/systemd/
git checkout main
git pull --ff-only origin main
git log --oneline -1
```

`--ff-only` refuses if the box has local commits. If it refuses, stop and
report; do not merge or reset on the box.

If `requirements.txt` changed:

```bash
/home/ubuntu/Wildfire-Services/.venv/bin/pip install -r requirements.txt
```

If anything under `deploy/systemd/` changed:

```bash
sudo cp /home/ubuntu/Wildfire-Services/deploy/systemd/wildfire-*.service /etc/systemd/system/
sudo systemctl daemon-reload
```

`wildfire-gpu-control` is copied too but stays disabled. Do not enable it.

### 2.1 First deploy with the risk unit

Only once, on the first deploy whose `deploy/systemd/` includes
`wildfire-risk-forecasting.service`. Check whether it is already enabled:

```bash
systemctl is-enabled wildfire-risk-forecasting 2>/dev/null || echo "not installed"
```

If it prints `enabled`, skip to section 3. Otherwise:

```bash
ss -ltnp | grep ':8001' || echo "nothing listening on 8001"
```

If something already listens on 8001 (for example a uvicorn started inside
`screen`), note how it was started, then stop it, or the unit fails to bind.
Confirm the model artifacts the unit needs are present:

```bash
ls -l services/risk_forecasting/artifacts/cnhpp_params.npz
ls services/risk_forecasting/data | head
```

Then install and enable:

```bash
sudo cp /home/ubuntu/Wildfire-Services/deploy/systemd/wildfire-risk-forecasting.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wildfire-risk-forecasting
curl -s --max-time 15 http://127.0.0.1:8001/health | python3 -m json.tool
```

`/health` returns 200 even when the model failed to load; read the body and
confirm the model is loaded. If it is not, the data files are missing or in
a different place (`RISK_FORECASTING_DATA_DIR`); fix that before continuing.
More detail in `deploy/systemd/SYSTEMD_SETUP.md`.

## 3. Restart services

```bash
sudo systemctl restart \
  wildfire-data-query \
  wildfire-risk-forecasting \
  wildfire-visualization \
  wildfire-comparison \
  wildfire-agent \
  wildfire-frontend
sudo systemctl --no-pager --full status \
  wildfire-data-query wildfire-risk-forecasting wildfire-visualization \
  wildfire-comparison wildfire-agent wildfire-frontend | grep -E '^(●|\s+Active:)'
```

All six should show `active (running)`. The agent can be `active` for
several minutes while `/health` still fails, because it waits for the model
host to warm up. Do not restart it again in that window. Watch it:

```bash
journalctl -u wildfire-agent -f
```

Wait for the uvicorn startup line or the "model unavailable at startup" line,
then Ctrl+C.

## 4. Health checks

Quick check of every port:

```bash
for port in 8000 8001 8002 8003 8004; do
  printf '%s ' "$port"; curl -s -o /dev/null -w '%{http_code}\n' --max-time 15 "http://127.0.0.1:$port/health"
done
printf '8765 '; curl -s -o /dev/null -w '%{http_code}\n' --max-time 15 http://127.0.0.1:8765/
```

Every line should end in `200`. The agent health body also reports the model
and each downstream service:

```bash
curl -s --max-time 15 http://127.0.0.1:8004/health | python3 -m json.tool
```

Then run the smoke test. Health checks only first, then the full run with the
5 `/ask` questions (these can take up to 15 minutes in total on the CPU model
host):

```bash
SMOKE_SKIP_ASK=1 bash scripts/smoke_test.sh
bash scripts/smoke_test.sh
```

Reading the result:

- Exit code 0 and `failed: 0` means the deploy is healthy.
- Checks tagged `[requires PR #22]` (multi-utility, Modesto, EPSS ranking,
  live) are expected to fail until PR #22 (`router-paraphrase-fixes`) is merged
  and deployed. They are listed as "failed but waiting on PR #22" and do not
  change the exit code. They are not a regression.
- Once the deployed commit includes PR #22, run
  `SMOKE_PR22_MERGED=1 bash scripts/smoke_test.sh` so those checks count.

If the smoke test fails on a check without the PR #22 tag, roll back
(section 8).

## 5. Jev shadow mode

Shadow mode sends each question to Jev (TypeSafe) in the background and
writes Jev's decision next to the router's decision in a JSONL log. Users
never wait on it and never see its output.

**Precondition: the Jev code must be on the deployed commit.** As of
`c108380` (PR #21), `main` has no Jev code and ignores every `AGENT_JEV_*`
variable. Check before you continue:

```bash
test -f services/agent/decisions/shadow.py && echo "Jev code present" || echo "Jev code NOT on this commit; stop here"
```

If it is not present, skip this section. It applies once the `jev-shadow`
branch has merged into `main` and been deployed.

### 5.1 Edit .env

Put the log outside the repository, so a `git checkout` for rollback or a
fresh clone never touches it. (`services/agent/logs/`, the code default, is
gitignored as well, so a log left there no longer breaks the clean-tree check
in step 1, but keep production logs outside the repo anyway.)

```bash
mkdir -p /home/ubuntu/wildfire-logs
cp /home/ubuntu/Wildfire-Services/.env /home/ubuntu/wildfire-logs/.env.before-shadow.$(date +%Y%m%d%H%M%S)
chmod 600 /home/ubuntu/wildfire-logs/.env.before-shadow.*
nano /home/ubuntu/Wildfire-Services/.env
```

Set these lines (add any that are missing; change any that exist, do not
duplicate them):

```
AGENT_JEV_MODE=shadow
AGENT_JEV_LOG_PATH=/home/ubuntu/wildfire-logs/jev_shadow.jsonl
AGENT_JEV_DAILY_CALL_CAP=500
```

- `AGENT_JEV_DAILY_CALL_CAP` counts user questions per UTC day, not API
  calls. Past the cap, questions are logged as `dropped` with reason
  `daily_cap` and Jev is not called. The code default is 5000; start at 500
  and raise it once the cost report (5.3) shows real per-question spend.
- `TYPESAFE_API_KEY` must already be set. Check without printing it:

```bash
grep -c '^TYPESAFE_API_KEY=.\+' /home/ubuntu/Wildfire-Services/.env   # expect 1
grep -E '^AGENT_JEV_(MODE|LOG_PATH|DAILY_CALL_CAP)=' /home/ubuntu/Wildfire-Services/.env
```

systemd reads `.env` strictly: no `export`, no spaces around `=`, no `$VAR`,
and quote any value containing `#`. Check with the command in
`deploy/systemd/SYSTEMD_SETUP.md` before restarting.

Leave `AGENT_JEV_MODE` at `shadow`. Do not set `tool_pick`,
`tool_pick_template`, or `plan` in production; those let Jev change answers.

### 5.2 Restart and verify the log grows

```bash
sudo systemctl restart wildfire-agent
journalctl -u wildfire-agent -n 50 --no-pager | grep -iE 'jev|error' || true
LOG=/home/ubuntu/wildfire-logs/jev_shadow.jsonl
wc -l "$LOG" 2>/dev/null || echo "no log yet (expected before the first question)"
curl -s --max-time 900 -H 'Content-Type: application/json' \
  -d '{"question": "How many PG&E utility-attributed ignitions were there in 2024?"}' \
  http://127.0.0.1:8004/ask -o /dev/null -w '%{http_code}\n'
sleep 10
wc -l "$LOG"
tail -n 3 "$LOG" | python3 -c 'import json,sys; [print(r.get("type"), r.get("error"), (r.get("regex") or {}).get("rule")) for r in map(json.loads, sys.stdin)]'
```

Expect the line count to go up by at least one after the question, and a
`routing` row with error `None`. If rows show an error such as an HTTP 401,
the key is wrong; if no rows appear at all, confirm `AGENT_JEV_MODE=shadow`
reached the process:

```bash
sudo cat /proc/$(systemctl show -p MainPID --value wildfire-agent)/environ | tr '\0' '\n' | grep '^AGENT_JEV_MODE='
```

(That prints only the mode line, never the key.)

### 5.3 Daily report

```bash
cd /home/ubuntu/Wildfire-Services
PYTHONPATH=. .venv/bin/python -m services.agent.eval.shadow_report \
  --log /home/ubuntu/wildfire-logs/jev_shadow.jsonl \
  --out /home/ubuntu/wildfire-logs/shadow_report_$(date +%Y%m%d).md
```

It prints question counts, the router versus Jev disposition table with the
most confident disagreements, the Jev confidence distribution and the share
under 0.8, `tool_pick_decision` rows by path and reason, and estimated cost
at $0.042 per million input tokens. Stop and report if estimated spend passes
$5 for the period you are reviewing.

The log rotates at `AGENT_JEV_LOG_MAX_MB` (default 50) into `.1` to `.5`; the
report reads all of them.

### 5.4 Turn shadow mode off

Set `AGENT_JEV_MODE=off` in `.env`, then `sudo systemctl restart wildfire-agent`.

## 6. Restrict port 8004 to CloudFront

Today port 8004 may be open to the internet. Only CloudFront should reach it.
AWS publishes the CloudFront origin-facing addresses as the managed prefix
list `com.amazonaws.global.cloudfront.origin-facing`.

Before you start:

- Confirm the website calls the agent through CloudFront, not the instance IP.
  Anything calling `http://<instance ip>:8004` directly breaks after this.
- Session Manager does not need any inbound rule; you will not lose your shell.
- Calls from the host itself (`127.0.0.1`, the smoke test, `/home/ubuntu/jev-eval`)
  do not pass through the security group and are not affected.
- The CloudFront prefix list counts as 55 rules toward the security group
  rule quota (default 60 inbound rules per group). If the group is near the
  quota, the add fails; request a quota increase or use a dedicated group.

Do the add before the remove so there is no gap.

### 6.1 Console

1. VPC console, Managed prefix lists. Search
   `com.amazonaws.global.cloudfront.origin-facing`. Note its ID (`pl-...`);
   the ID differs by region.
2. EC2 console, Instances, select the backend instance, Security tab. Note the
   security group ID (`sg-...`). If there are several, find the one whose
   inbound rules include port 8004.
3. Open that security group, Inbound rules, Edit inbound rules.
4. Add rule: Type Custom TCP, Port range `8004`, Source Custom, choose the
   `pl-...` prefix list. Description `CloudFront origin-facing to agent`.
   Save.
5. Edit inbound rules again. Delete every other rule for port 8004 (for
   example `0.0.0.0/0` or `::/0`). Save.
6. Verify (6.3).

### 6.2 AWS CLI (written out, not run by the author of this runbook)

Run from a machine with AWS credentials for the account, or from CloudShell.
Replace the placeholders.

```bash
REGION=us-west-2              # the backend's region
INSTANCE_ID=i-xxxxxxxxxxxxxxxxx

# 1. Prefix list ID
PL_ID=$(aws ec2 describe-managed-prefix-lists --region "$REGION" \
  --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
  --query 'PrefixLists[0].PrefixListId' --output text)
echo "$PL_ID"

# 2. Security groups on the instance
aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].SecurityGroups' --output table
SG_ID=sg-xxxxxxxxxxxxxxxxx    # the group that has the 8004 rule

# 3. Current 8004 rules (note the SecurityGroupRuleId of each one to remove)
aws ec2 describe-security-group-rules --region "$REGION" \
  --filters Name=group-id,Values="$SG_ID" \
  --query 'SecurityGroupRules[?IsEgress==`false` && FromPort<=`8004` && ToPort>=`8004`].[SecurityGroupRuleId,CidrIpv4,CidrIpv6,PrefixListId,Description]' \
  --output table

# 4. Add the CloudFront rule first
aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
  --ip-permissions "IpProtocol=tcp,FromPort=8004,ToPort=8004,PrefixListIds=[{PrefixListId=$PL_ID,Description='CloudFront origin-facing to agent'}]"

# 5. Remove the old open rules, using the IDs from step 3
aws ec2 revoke-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
  --security-group-rule-ids sgr-xxxxxxxxxxxxxxxxx sgr-yyyyyyyyyyyyyyyyy

# 6. Confirm only the prefix list rule remains for 8004
aws ec2 describe-security-group-rules --region "$REGION" \
  --filters Name=group-id,Values="$SG_ID" \
  --query 'SecurityGroupRules[?IsEgress==`false` && FromPort<=`8004` && ToPort>=`8004`].[SecurityGroupRuleId,CidrIpv4,PrefixListId]' \
  --output table
```

If a rule covers a port range that includes 8004 and other ports (for example
8000 to 8765), do not revoke it blindly: replace it with rules for the other
ports first, then revoke it.

### 6.3 Verify

- From your laptop (not the instance): `curl -m 10 http://<instance public ip>:8004/health`
  should time out.
- The website Ask panel, through CloudFront, should still answer.
- To undo: add back the removed rule, then remove the prefix list rule.

## 7. Rotate the TypeSafe key

Do this after any exposure, and on a schedule.

1. In the TypeSafe console, create a new API key. Do not revoke the old one
   yet.
2. On the host, back up `.env` and replace the key without echoing it. The
   script reads the key from a hidden prompt and rewrites only the
   `TYPESAFE_API_KEY` line:

```bash
cd /home/ubuntu/Wildfire-Services
BACKUP=/home/ubuntu/wildfire-logs/.env.before-rotate.$(date +%Y%m%d%H%M%S)
mkdir -p /home/ubuntu/wildfire-logs && cp .env "$BACKUP" && chmod 600 "$BACKUP"
python3 - <<'PYEOF'
import getpass
import pathlib

path = pathlib.Path(".env")
key = getpass.getpass("New TYPESAFE_API_KEY (hidden): ").strip()
if not key or any(ch in key for ch in " #\"'"):
    raise SystemExit("Key is empty or has a character systemd will not read; nothing changed.")
lines = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("TYPESAFE_API_KEY=")]
lines.append(f"TYPESAFE_API_KEY={key}")
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("TYPESAFE_API_KEY updated.")
PYEOF
chmod 600 .env
grep -c '^TYPESAFE_API_KEY=' .env    # expect 1
sudo systemctl restart wildfire-agent
```

3. Verify with a question and the shadow log, as in 5.2: new `routing` rows
   should have `error` null. An authorization error means the new key is
   wrong; restore the backup (`cp "$BACKUP" .env`) and restart.
4. Update every other copy of the key: the eval worktree
   `/home/ubuntu/jev-eval/.env` if it has one, and developer machines (each
   local worktree has its own `.env` copy).
5. Revoke the old key in the TypeSafe console.
6. Delete the backups that hold the old key:
   `rm /home/ubuntu/wildfire-logs/.env.before-rotate.*`

## 8. Rollback

Use the commit recorded in step 1 (last line of
`/home/ubuntu/deploy_history.txt`, or ask whoever deployed).

```bash
cd /home/ubuntu/Wildfire-Services
PREV=$(tail -n 1 /home/ubuntu/deploy_history.txt)   # recorded in step 1, before the pull
git log --oneline -1 "$PREV"                          # confirm it is the one you expect
git checkout --detach "$PREV"
```

Step 1 appends the commit that was running before the pull, so the last line
is the one to return to. If step 1 was skipped or run twice, read the file and
pick the commit by hand (`git reflog` also shows where `main` was before the
pull).

If `requirements.txt` or `deploy/systemd/` changed in the deploy, reinstall
and recopy from the rolled-back tree:

```bash
.venv/bin/pip install -r requirements.txt
sudo cp deploy/systemd/wildfire-*.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Restart and check:

```bash
sudo systemctl restart wildfire-data-query wildfire-risk-forecasting wildfire-visualization wildfire-comparison wildfire-agent wildfire-frontend
bash scripts/smoke_test.sh   # if the rolled-back commit has it
```

The repo is now on a detached HEAD. That is deliberate: it records that
production is not on `main`. After the fix merges, return with
`git checkout main && git pull --ff-only origin main` and redeploy from step 1.

If only shadow mode is the problem, you do not need a code rollback: set
`AGENT_JEV_MODE=off` and restart the agent (5.4).

Record what happened (time, commit, symptom) in the team channel.
