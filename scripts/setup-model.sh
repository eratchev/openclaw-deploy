#!/bin/bash
# Switch interactive chat primary model to gpt-4o-mini and restart openclaw.
# Run once on existing deployment; entrypoint.sh handles fresh deployments.
set -euo pipefail

HOST="${1:-}"
if [ -z "$HOST" ]; then
    echo "Usage: $0 user@host"
    exit 1
fi

COMPOSE="sudo docker compose -f ~/openclaw-deploy/docker-compose.yml"

echo "▶ Updating model config on $HOST..."

ssh "$HOST" "$COMPOSE exec -T openclaw python3 /dev/stdin" << 'PYEOF'
import json
p = '/home/node/.openclaw/openclaw.json'
with open(p) as f:
    cfg = json.load(f)

# Locate the defaults section — path varies by OpenClaw version
d = None
for keys in [['agents', 'main', 'defaults'], ['defaults'], ['agents', 'defaults']]:
    obj = cfg
    try:
        for k in keys:
            obj = obj[k]
        if 'model' in obj:
            d = obj
            break
    except (KeyError, TypeError):
        continue

if d is None:
    cfg.setdefault('defaults', {})
    d = cfg['defaults']

d.setdefault('model', {})['primary'] = 'openai/gpt-4o-mini'
d['model']['fallbacks'] = ['anthropic/claude-haiku-4-5-20251001']

# Cap context window — 40K was burning ~22M input tokens/day on gpt-4o-mini.
# 16000 is the platform-enforced floor; lower values fail with FailoverError.
d['contextTokens'] = 16000

models = d.setdefault('models', {})
models['openai/gpt-4o-mini'] = {'alias': 'GPT'}
models.pop('openai/gpt-5.1-codex', None)

with open(p, 'w') as f:
    json.dump(cfg, f, indent=4)

print(f'openclaw.json updated')
print(f'  primary:       {d["model"]["primary"]}')
print(f'  fallbacks:     {d["model"]["fallbacks"]}')
print(f'  contextTokens: {d["contextTokens"]}')
PYEOF

ssh "$HOST" "cd ~/openclaw-deploy && sudo docker compose restart openclaw"
echo "✓ openclaw restarted with new model config."
echo "  Run 'make logs' to verify gpt-4o-mini is being used."
