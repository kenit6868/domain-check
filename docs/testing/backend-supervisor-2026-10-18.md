# Unified backend startup — 2026-10-18

Historical initial launcher validation. The subsequent per-type bounded workers,
team deployment and fair URL turns are documented in ../team-deployment.md and
team-capacity-2026-10-18.md; the initial three-child layout below was superseded.

Standard local startup is now `python -m domaincheck` from the backend environment,
or `backend/run_backend.bat`. The installed console entry point is
`domaincheck-backend`. It starts API, all-job worker and Cloudflare runner using
the same Python executable, environment and inherited console output.

API `--reload` applies only to the API subprocess. The supervisor holds a local
singleton socket (127.0.0.1:8790 by default), returns failure if any child exits,
signals all remaining child process groups and waits for shutdown without a
force-kill deadline. Ctrl+C/SIGTERM also stops the children. It does not restart
failed services automatically. Frontend and DB/cache are separate prerequisites.

Raw uvicorn is still API-only for explicitly separated deployments. Stop former
standalone workers when idle before switching; the singleton socket cannot
detect manually launched workers. Never combine the new launcher with the old
worker-only shortcuts. Worker code edits require restarting the stack when idle.

Validation:

- Six new unit tests passed: child exit propagation, partial startup cleanup,
  interrupt cleanup without force kill, API-only reload, no-launch dry-run and
  duplicate-launch prevention.
- Full backend discovery: 380 tests, OK, 173 skipped (207 passed). This run did
  not configure isolated PostgreSQL/Redis or the wheel fixture.
- Subsequently built a fresh wheel with isolated build dependencies; packaging
  tests passed 3/3 using it, including outside-repository checks.
- Extracted-wheel launcher dry-run passed with isolated Python outside the repo.
- Windows BAT dry-run, compileall, pip check and diff whitespace checks passed.

No real API/worker processes were started or restarted, no queued jobs claimed,
and no mail/provider activity performed. Runtime process shutdown has mock test
coverage; a live production stack transition was deliberately not performed.
