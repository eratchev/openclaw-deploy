# Memory Index

- [project_status.md](project_status.md) — Feature completion status; heartbeat + cron complete as of 2026-03-19
- [deploy_workflow.md](deploy_workflow.md) — `make push` is the single non-interactive deploy command after every git push
- [feedback_workflow.md](feedback_workflow.md) — After every change: update docs, run tests, deploy and verify on VPS
- [feedback_no_pii.md](feedback_no_pii.md) — Never commit PII or sensitive info (IPs, chat IDs, usernames) to git
- [reference_api_key_rotation.md](reference_api_key_rotation.md) — Rotating Anthropic API key requires updating both .env AND auth-profiles.json on the volume
