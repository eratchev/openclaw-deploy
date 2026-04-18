---
name: Anthropic API key rotation procedure
description: How to rotate the Anthropic API key in OpenClaw — env var alone is not enough, must also update auth-profiles.json on the volume
type: reference
---

Rotating the Anthropic API key requires updating **two places**:

1. **`.env` on the server** — `ANTHROPIC_API_KEY=<new key>`
2. **`auth-profiles.json` on the OpenClaw data volume** — OpenClaw stores the key here and reads it at startup, not from the env var on each request.

   Path: `/home/node/.openclaw/agents/main/agent/auth-profiles.json`
   Field: `profiles.anthropic:default.key`

   Also clear any cooldown state in `usageStats.anthropic:default` (remove `cooldownUntil`, `cooldownReason`, `errorCount`, `failureCounts`, `lastFailureAt`).

3. **Restart the container** (not recreate — `docker compose restart openclaw` is enough since auth-profiles.json is on the volume).

**Why:** `docker compose up -d` (recreate) picks up the new env var but OpenClaw ignores it in favour of the cached key in auth-profiles.json. Without updating auth-profiles.json, all Anthropic API calls will fail with HTTP 401.

**Quick command to update the key from inside the container:**
```bash
ssh HOST "sudo docker compose -f ~/openclaw-deploy/docker-compose.yml exec -T openclaw python3 /dev/stdin" << 'PYEOF'
import json, os
p = '/home/node/.openclaw/agents/main/agent/auth-profiles.json'
with open(p) as f:
    data = json.load(f)
data['profiles']['anthropic:default']['key'] = os.environ['ANTHROPIC_API_KEY']
stats = data.get('usageStats', {}).get('anthropic:default', {})
for k in ['cooldownUntil','cooldownReason','errorCount','failureCounts','lastFailureAt']:
    stats.pop(k, None)
with open(p, 'w') as f:
    json.dump(data, f, indent=2)
print('Done')
PYEOF
```
