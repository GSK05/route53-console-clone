# Route 53 Console Clone

A Next.js and FastAPI application that recreates the key Route 53 hosted zone and DNS record workflows. It stores data in SQLite and **does not serve DNS queries or connect to AWS**. The UI follows the assignment's supplied screenshots, including the hosted zones table, zone detail, quick record form, and edit drawer.

## Features

- Mock login, logout, and a seven-day HTTP-only cookie session.
- Public and private hosted zones with search, pagination, create, view, comment edit, and delete.
- A, AAAA, CNAME, TXT, MX, NS, PTR, SRV, and CAA records with validation and persistent CRUD.
- Route 53-style tables, filters, notifications, confirmation dialogs, navigation, and placeholder sections.
- BIND zone-file import; JSON and BIND export; dark mode; keyboard shortcuts; bulk record deletion.
- Default NS and SOA records for public zones. These are displayed but protected from editing/deletion.

The app follows AWS's hosted zone edit behavior: the description/comment can be edited, but the name and public/private type cannot be changed after creation. It supports simple routing and non-alias records. AWS-specific alias targets, VPC discovery, DNS resolution, billing, IAM, and advanced routing policies are outside the assignment's core CRUD scope.

## Run locally

Requirements: Node.js 22+, Python 3.13+, and npm.

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

In another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open <http://localhost:3000>. The default login is `demo` / `route53demo`. Set `DEMO_USER` and `DEMO_PASSWORD` on the backend to change it. `DATABASE_PATH` defaults to `backend/route53.db`. The Next.js proxy uses `BACKEND_URL`, which defaults to `http://127.0.0.1:8000`.

Alternatively, run `docker compose up --build` from the repository root. The Compose setup stores SQLite in the named `route53_data` volume, so data survives container recreation. Copy `.env.example` to `.env` to customize the demo credentials. When serving over HTTPS, set `COOKIE_SECURE=true`.

## Architecture

```text
Browser → Next.js App Router UI → /api proxy → FastAPI → SQLite
```

The UI uses one client-side console component for the AWS-style shell and screens. The FastAPI service owns authentication, validation, search, pagination, and file format handling. SQLite is the durable store. The `/api` rewrite keeps requests and the session cookie on the same origin.

### Database schema

| Table | Purpose | Key fields |
| --- | --- | --- |
| `sessions` | Mock login persistence | token, username, expires_at |
| `hosted_zones` | Public/private zones | id, name, type, comment, VPC fields, name_servers, timestamps |
| `tags` | Zone tags | zone_id, key, value |
| `records` | DNS record sets | id, zone_id, name, type, ttl, values_json, routing_policy, is_default, timestamps |

`records` has a unique constraint on `(zone_id, name, type)`, matching simple routing's single record set per name/type. Multiple values are stored in that record set. Foreign keys remove records and tags when a zone is deleted; the API first requires non-default records to be removed.

### API overview

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/auth/login` | Create session |
| POST | `/api/auth/logout` | End session |
| GET | `/api/auth/me` | Check session |
| GET, POST | `/api/zones` | Search/list and create zones |
| GET, PATCH, DELETE | `/api/zones/{id}` | View, edit comment, delete zone |
| GET, POST | `/api/zones/{id}/records` | Search/list and create record sets |
| PUT, DELETE | `/api/zones/{id}/records/{record_id}` | Edit and delete a record set |
| POST | `/api/zones/{id}/records/bulk-delete` | Delete selected record sets |
| POST | `/api/zones/{id}/import` | Import BIND text (`{ "content": "..." }`) |
| GET | `/api/zones/{id}/export?format=json|bind` | Download a zone |
| GET | `/api/health` | Health check |

Interactive API docs are at `/docs` on the backend service. All zone and record endpoints require the session cookie.

## Keyboard shortcuts

`/` focuses the current table search, `N` starts zone or record creation, `Esc` closes dialogs, and `?` shows shortcut help.

## Verification

From `backend/`, run `python -m unittest -v test_api.py`. From `frontend/`, run `npm run lint` and `npm run build` after installing dependencies. The backend tests exercise authentication, persistence, CRUD, validation, import/export, and bulk deletion.

## Deployment

Repository name: **`GSK05/route53-console-clone`**. A single Docker host or platform with a persistent volume is the simplest deployment. Run the Compose setup behind HTTPS, set a non-default `DEMO_PASSWORD`, and persist `/data`. A deployment that discards its filesystem will also discard SQLite data, so an ephemeral backend host is unsuitable without an attached persistent disk.
