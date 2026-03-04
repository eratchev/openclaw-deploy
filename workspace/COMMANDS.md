# COMMANDS.md - Global Commands

These commands work in all sessions. In Telegram groups, @mention the bot: `@eratOpenClawBot <command>`.

## ai update

When someone sends "ai update" (or similar), use the **web_search tool** (NOT web_fetch) to search for recent AI news. Run 2-3 searches like: "latest LLM releases 2026", "AI agent frameworks news 2026", "LLM benchmarks pricing March 2026". Then summarize up to 10 bullets on recent developments relevant to coding-capable LLMs, agent/tool frameworks, benchmarks, long-context, and pricing/API changes. For each bullet: why it matters to backend/infra engineers, impact on coding agents and OpenClaw architectures, one practical experiment. Avoid hype.

**Never use web_fetch for this — use web_search only.**
