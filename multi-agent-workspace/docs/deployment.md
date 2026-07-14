# Deployment

## Local (recommended for the MVP)

```bash
npm install && cp .env.example .env && npm run setup && npm run build && npm start
```

Health endpoint: `GET /api/health` (checks DB connectivity).

## Docker

```bash
docker compose up --build
```

- The SQLite database lives on the `workspace-data` volume (`/data/workspace.db`), so history survives container restarts — pair this with the boot-time interrupted-run recovery.
- Provider keys are passed through from your shell / `.env` via compose `environment` entries.
- The container applies the schema and (idempotently) seeds on boot.

## Production notes

This MVP is a single-user tool. Before multi-user or internet-facing deployment you should (in order):

1. Add session authentication + RBAC (schema already carries actor attribution everywhere).
2. Move to PostgreSQL (change the Prisma datasource provider, regenerate, migrate) and introduce `prisma migrate` migrations instead of `db push`.
3. Extract the engine into a worker process behind a durable queue (BullMQ/Redis); `createRun`/`processRun` is the seam.
4. Put the app behind TLS and add API rate limiting.
5. Review `docs/security.md` for the full gap list.

## Operations

- **Backups**: copy the SQLite file (or volume). All history is in the DB.
- **Restart behavior**: runs that were mid-flight are marked `interrupted` at boot and can be resumed from the UI.
- **Logs**: structured console logs from the engine (`[engine]`, `[api]`, `[boot]` prefixes).
