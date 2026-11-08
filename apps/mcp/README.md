# Scout MCP server

Ask Claude about Scout from Claude Code or Claude Desktop: "show today's top fresher AI jobs",
"what follow-ups are due?", "mark the algoleap job applied", "let's label jobs for the eval".

Runs locally over stdio and talks to the Scout Postgres through `scout_core` (same feed rules and
services as the API and Telegram). Nothing listens on the network. Needs `DATABASE_URL` in the repo's `.env`.

| Tool | Kind | What it does |
|---|---|---|
| `top_jobs` | read | Open entry-level AI jobs I haven't acted on, best match first |
| `job_details` | read | Score parts and reasons, matched/missing skills, entry-level evidence, posting text (marked untrusted) |
| `stats` | read | New / applied in 24 h, follow-ups due, open in feed, eval-label progress |
| `pipeline` | read | Applications with dates, notes, follow-up due |
| `jobs_to_label` | read | Unlabelled scored jobs for the matching eval (full descriptions first, high and low scores mixed) |
| `set_job_status` | write | applied / skipped / interview / offer / rejected / back to discovered (audited as `claude-mcp`) |
| `label_job` | write | apply / maybe / no label for `cli match-eval` (audited as `claude-mcp`) |

## Use it

Claude Code in this repo picks it up from `.mcp.json` (approve the `scout` server once).
Claude Desktop: add to `claude_desktop_config.json`:

```json
{"mcpServers": {"scout": {"command": "uv", "args": ["run", "--quiet", "--directory", "/path/to/scout", "scout-mcp"]}}}
```

Tests: `uv run pytest apps/mcp` (real Postgres via the repo's test fixtures).
