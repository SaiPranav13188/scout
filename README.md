# Scout

Personal job hunter for fresher and entry-level roles. It finds new jobs, filters out anything that needs
more experience than you have or that you can't take from India, checks the company looks legitimate, and puts
the rest in a dashboard. Clicking a job opens the original posting; you apply yourself.

- Spec: kept private (personal details); the plan below summarises it
- Plan and decisions: [docs/PLAN.md](docs/PLAN.md)
- Status: [PROGRESS.md](PROGRESS.md)
- Setup and credentials: [docs/SETUP.md](docs/SETUP.md)
- Evaluation numbers: [docs/EVALS.md](docs/EVALS.md)

```
apps/web         Next.js dashboard
apps/api         FastAPI
apps/worker      Celery workers + scheduler (+ CLI: python -m scout_worker.cli)
packages/shared  sources, normalize, dedupe, filters, trust, verification, DB models, migrations
```

Tests: `uv run pytest` (uses an embedded Postgres, no Docker needed).
