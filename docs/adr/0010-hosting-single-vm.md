# 0010. Hosting on a single VM

Status: Accepted (2026-10-14). Updated 2026-10-29 with the VM as deployed in Phase 3.

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

## Update (2026-10-29): the VM as deployed in Phase 3

The public demo and the always-on watcher run on one VM, as decided. What differs from the text above:

- The VM: AWS EC2 t3.xlarge in eu-north-1 (the decision named eu-west-2), Ubuntu 26.04.1 (the decision named 24.04), Elastic IP 13.49.146.96.
- Caddy v2.11.7, installed from its official apt repository, serves the public demo over HTTPS. The Caddyfile is `deploy/Caddyfile`. The name is `13-49-146-96.sslip.io`, which resolves to the Elastic IP, with a Let's Encrypt certificate that Caddy gets over port 80 and renews itself (issuer Let's Encrypt YE2, valid to 26 Jan 2027 when checked). Plain http is redirected to https. sslip.io is a free third-party DNS name, so the demo depends on it.
- The demo is a Docker container, `steward-demo`, built from `deploy/demo/Dockerfile`. It has a read-only root, a tmpfs `/tmp`, all capabilities dropped and no-new-privileges, restarts unless stopped, and publishes `127.0.0.1:8100` only. It holds no key, no pass and no owner token.
- The security group now opens ports 80 and 443, for Caddy only. Port 8000 stays closed: a request from the internet times out. The owner reaches Steward through an SSH tunnel.

| Port | Used by | Exposure |
| --- | --- | --- |
| 80, 443 | Caddy | Open to the internet in the security group, for Caddy only |
| 8100 | Demo container | `127.0.0.1` on the VM, reached by Caddy |
| 8000 | Steward | Closed in the security group; sandboxes reach it as `host.openshell.internal`, the owner through the SSH tunnel |

- The forward to Hermes (port 8642 in the table above) is a systemd user unit, `deploy/steward-forward.service`. Before, it died whenever the VM stopped and was restarted by hand. The unit runs it in the foreground and restarts it every 10 seconds if it stops.
- Reboot test, 28 Oct: after `sudo systemctl reboot`, Steward came back healthy, the forward was active and Hermes answered its health check with 200, both sandboxes were Ready, the demo container was running with its two recordings loaded and Caddy was active, all without a manual step.
- Memory: two live evenings peaked at 2,670 MB and 2,606 MB of the VM's 15.8 GB, so a t3.large (8 GB) would do. The VM is still a t3.xlarge, and the resize is optional.
- The nightly backup built in Phase 3 copies the SQLite database to `var/backups` on the same VM (03:00, the last 7 kept, live sessions only). It is not the Postgres backup to S3 planned above. An uptime alert, a disk snapshot schedule and an AWS budget alert are not set up yet.
