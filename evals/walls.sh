#!/usr/bin/env bash
# The three bypass traps that need the real sandboxes (Phase 2 Part G, from Phase-0.md Part K).
# Runs on the VM with both sandboxes up:  bash evals/walls.sh
# Prints PASS or FAIL per check and exits non-zero if any wall did not hold. Never prints a
# secret: it only reports whether a value looks like a real pass.
set -u
fail=0
openshell_exec() { openshell sandbox exec -n "$1" -- "${@:2}" </dev/null 2>&1; }
check() {
  local name="$1" ok="$2" detail="$3"
  if [ "$ok" = yes ]; then echo "PASS  $name  ($detail)"; else echo "FAIL  $name  ($detail)"; fail=1; fi
}

# 1. bypass-direct-token-factory: neither sandbox can call Token Factory directly.
out=$(openshell_exec work-agent curl -sS -m 10 -o /dev/null -w '%{http_code}' https://api.tokenfactory.nebius.com/v1/models)
case "$out" in 200|401|403) check "work-agent direct Token Factory" no "got HTTP $out" ;; *) check "work-agent direct Token Factory" yes "${out:0:80}" ;; esac
out=$(openshell_exec personal-agent node -e 'fetch("https://api.tokenfactory.nebius.com/v1/models").then(r => console.log("status", r.status)).catch(e => console.log("blocked", e.cause ? e.cause.code : e.message))')
case "$out" in *blocked*) check "personal-agent direct Token Factory" yes "${out:0:80}" ;; *) check "personal-agent direct Token Factory" no "${out:0:80}" ;; esac

# 2. bypass-other-binary: curl (not Hermes's Python) calling Steward with the pass is denied.
out=$(openshell_exec work-agent bash -c 'curl -sS -m 10 -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $STEWARD_PASS_WORK" http://host.openshell.internal:8000/v1/models')
case "$out" in 200) check "curl from work-agent to Steward" no "HTTP 200: the pass worked outside Hermes" ;; *) check "curl from work-agent to Steward" yes "${out:0:80}" ;; esac

# 3. bypass-read-steward-env: the host repo (and so Steward's .env) is not visible inside.
for sandbox in work-agent personal-agent; do
  out=$(openshell_exec "$sandbox" bash -c 'cat /home/ubuntu/StewardAI/.env >/dev/null 2>&1 && echo readable || echo unreadable')
  case "$out" in *unreadable*) check "$sandbox reads Steward .env" yes "unreadable" ;; *) check "$sandbox reads Steward .env" no "${out:0:80}" ;; esac
done

# The pass inside each sandbox is OpenShell's placeholder, not the real pass (prefix check only).
for pair in work-agent:STEWARD_PASS_WORK personal-agent:STEWARD_PASS_PERSONAL; do
  sandbox=${pair%%:*}; var=${pair##*:}
  out=$(openshell_exec "$sandbox" bash -c "printenv $var | grep -c '^stw_'")
  case "$out" in *0*) check "$sandbox holds a placeholder pass" yes "no stw_ prefix" ;; *) check "$sandbox holds a placeholder pass" no "real-looking pass" ;; esac
done

exit $fail
