# GPU control service

Optional legacy service for starting and stopping an EC2 instance that runs
Ollama. This is not the agent and not data_query. The current test deployment
uses the CPU-hosted model configured by `AGENT_MODEL_BASE_URL`; it does not
require this service.

There are deliberately no instance, Ollama URL, or model defaults. Configure
`GPU_INSTANCE_ID`, `GPU_OLLAMA_URL`, and `GPU_MODEL` to reactivate it for a new
resource. Without all three, `/health` reports `disabled`, `/gpu/status` reports
`unavailable`, and start/stop return 503 without calling EC2.

## Run

```bash
# PowerShell: $env:PYTHONPATH = "."
# Required before POST start/stop will work:
# GPU_INSTANCE_ID, GPU_OLLAMA_URL, GPU_MODEL, GPU_CONTROL_TOKEN,
# GPU_AWS_REGION (or AWS_REGION / AWS_DEFAULT_REGION), and instance role credentials
# Optional: GPU_AGENT_URL (agent used for the pre-fire; default http://127.0.0.1:8004)
uvicorn services.gpu_control.app:app --port 8005 --app-dir .
```

| Method | Path | Auth |
|---|---|---|
| `GET` | `/health` | none |
| `GET` | `/gpu/status` | none |
| `POST` | `/gpu/start` | `X-GPU-Control-Token` |
| `POST` | `/gpu/stop` | `X-GPU-Control-Token` |

Missing `GPU_CONTROL_TOKEN` → POST returns **503** (start is never open).
Wrong or missing header → **401**. An EC2 start/stop API error → **502**.

`GET /gpu/status` is pollable. Concurrent `POST /gpu/start` is serialized with
an in-process lock. If a start is already running, or state is not `stopped` /
`error`, the handler returns the current status payload and does **not** call
`StartInstances` again. The lock resets when this process restarts.

When explicitly configured, `POST /gpu/start` returns immediately after
`StartInstances` (`state: starting`)
and a background task then:

1. Polls until Ollama answers (same `/api/ps` probe as status).
2. If the model is not in VRAM, loads it with the agent's
   `ensure_context_loaded()` path (same `num_ctx` / options as Ask).
3. Pre-fires `POST /ask` on the agent at `GPU_AGENT_URL`:
   `How many CPUC ignitions were there in 2023?`

`ready` requires the model resident **and** that pre-fire to return
`status=answer`. A failed or timed-out pre-fire is `error` with `reason`,
not a silent `ready`. `running` alone is not ready.

ETA is only present while this process saw `POST /gpu/start` and the state
is `starting` or `loading_model`. Restart the control service mid-boot and
ETA is omitted.

Stopping EC2 does **not** stop the EBS volume (~$20/month). There is no idle
auto-stop and no implicit start from Ask or `/health`.

## IAM (attach to `wildfire-backend-ssm-role`)

Do not apply this from the repo. Paste in the IAM console after substituting
`REGION` and `ACCOUNT_ID`. `ec2:DescribeInstances` does not support
resource-level ARNs, so that statement must use `*`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "StartStopGpuInstance",
      "Effect": "Allow",
      "Action": [
        "ec2:StartInstances",
        "ec2:StopInstances"
      ],
      "Resource": "arn:aws:ec2:REGION:ACCOUNT_ID:instance/YOUR_INSTANCE_ID"
    },
    {
      "Sid": "DescribeInstancesStarRequiredNoResourceScope",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances"
      ],
      "Resource": "*"
    }
  ]
}
```
