# fontem-community-api

FastAPI service for community features: user accounts, saved reports, comments, rate-limited per-client. Backed by its own Postgres database (gmr_app, namespace-scoped). Separate from the main fontem-api so community write traffic can't impact the read-heavy data API.

## Deploy

CI auto-deploys to the testing env on every merge to main. Promotion to staging / prod is **manual** — bump the version in `gitops/<env>/<service>.yaml` to land it in a given environment.

## Convention

See [/config/repos/CLAUDE.md](https://contribute.void42.internal/fontem/gitops) for workspace-wide rules (feature branches + CI gate, no direct push to main, full gate before declaring done, conventional commits).
