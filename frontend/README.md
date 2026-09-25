# GRC Audit Swarm — React Frontend

React + Vite + TypeScript UI for the GRC Audit Swarm platform. It talks to the
FastAPI backend in `src/api/` to launch audit runs, walk the human review
gates, and view the hashed evidence vault.

## Development

```bash
cd frontend
npm ci
npm run dev
```

This starts the Vite dev server against a local API. Vite proxies `/api`
requests to `http://localhost:8000`, so run the FastAPI backend separately
(see the repo root README).

`VITE_API_AUTH_TOKEN` is a **dev-only** convenience: set it in a local `.env`
file to have the dev server attach a bearer token to API requests. It gets
baked into the JS bundle at build time, so it must never be set for a
production build — see the comment in `src/api/client.ts` for details.

## Production

The frontend is built and served via Nginx, driven by the root
`docker-compose.yml`:

```bash
cd ..
docker compose up --build
```

In this setup nginx (see `nginx.conf.template`) injects the `Authorization`
header server-side when proxying to the API, so the built bundle carries no
token of its own.

## Checks

```bash
npm run lint
npm run build
```
