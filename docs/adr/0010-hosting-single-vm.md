# 0010. Hosting on a single VM

Status: Accepted (2026-10-14)

## Context

OpenShell runs the sandboxes as Docker containers, so the agents need an always-on Linux Docker host. The laptop is Windows with 7.3 GB of RAM, and OpenShell on WSL2 is experimental, so it is not the host. The plan uses AWS credits for hosting.

## Decision

One AWS EC2 t3.xlarge (4 vCPU, 16 GB RAM) in eu-west-2, running Ubuntu Server 24.04, with an 80 GiB gp3 disk and one Elastic IP. The security group allows SSH from the user's IP only.

It runs the OpenShell gateway, both sandboxes and Steward, and later Postgres and the console (Docker Compose). The disk is generous because it can grow but not shrink, and Docker images pile up.

Development is VM only for the agents and OpenShell. The laptop is for code, git, docs and tests.

Steward runs on the host as the trusted component. It listens on port 8000 bound to 0.0.0.0, so sandboxes can reach it as `host.openshell.internal` (ADR 0006). The security group keeps 8000 closed to the internet.

| Port | Used by | Exposure |
| --- | --- | --- |
| 22 | SSH | Security group, the user's IP only |
| 8000 | Steward | Reached by sandboxes as `host.openshell.internal`; closed in the security group |
| 8642 | Hermes API server | Inside its sandbox; reached through `openshell forward` on host loopback |
| 8643 | Second Hermes API server (fallback only) | Same, through `openshell forward` |
| 17670 | OpenShell gateway | Host loopback only |

There is no HTTPS in Phase 0, because all traffic stays on the VM. Caddy adds HTTPS for the public demo in Phase 3.

Nebius Serverless Jobs are skipped for now, because the hackathon credits cover Token Factory only. Red-team and eval runs run on the VM.

## Consequences

- Cost, estimated and to be checked on the AWS pricing page: about $0.19 per hour for the VM (about $4.50 per day if left on), about $0.005 per hour for the public IPv4 address, and about $7 per month for the gp3 disk.
- A stopped instance keeps its disk and Elastic IP, so those costs continue. The plan is to stop the instance when not working until Phase 3.
- From Phase 3 the instance must stay on, for the overnight watcher and for judging (1 to 15 Dec). The security group also changes then, when Caddy serves the public demo.
- Every change goes laptop to git to VM (ADR 0003).
- If the VM goes down, the demo goes down. The plan adds an uptime alert and a nightly Postgres backup to S3 in Phase 3.

## Alternatives considered

A larger instance (t3.2xlarge). Needed only with NemoClaw (ADR 0007).

The laptop with WSL2. Too little memory, and OpenShell on WSL2 is experimental.

A home server. The product doc allows it, but the plan hosts on AWS credits, and the demo must stay up for judges until 15 Dec.

Nebius AI Cloud compute. There are no AI Cloud credits.
