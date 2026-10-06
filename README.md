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
npm ci
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

## BIND import

The importer supports explicit owners, `@` for the zone root, relative owner names, and omitted owners. A record beginning with a space or tab inherits the owner of the preceding record. Explicit owners must start in the first column. Blank lines, comments, and `$TTL` directives do not reset the preceding owner.

TTL values can be integer seconds or case-insensitive `w`, `d`, `h`, `m`, and `s` units. Combined values such as `1h30m` are supported. Both `$TTL` and individual record TTLs use the same parser, with a range of 0–2147483647 seconds. An omitted TTL uses `$TTL` when supplied; otherwise it inherits the preceding record's TTL, with 300 seconds as the initial fallback. TTL and `IN` class may appear in either order.

Example file for `example.com`:

```bind
$ORIGIN example.com.
$TTL 1h
www IN A 192.0.2.10
    IN A 192.0.2.11
mail IN 30m MX 10 mail.example.com.
    30m IN MX 20 backup.example.com.
```

This creates a `www` A record set with two values and a 3600-second TTL, and a `mail` MX record set with two values and an 1800-second TTL. Invalid input fails the import before any records are committed. Error messages include the source line where possible. Existing records are not overwritten; the importer rejects conflicting name/type combinations.

Parenthesized records and comments outside quoted strings are supported. Default apex NS and SOA records are skipped, but still establish an owner for later shorthand rows. `$ORIGIN` must match the selected hosted zone. `$INCLUDE`, `$GENERATE`, nested origin changes, and arbitrary escaped owner names are not supported. Use fully qualified targets with trailing dots for domain-valued record data.

## Keyboard shortcuts

`/` focuses the current table search, `N` starts zone or record creation, `Esc` closes dialogs, and `?` shows shortcut help.

## Verification

From `backend/`, install `pip install -r requirements-dev.txt`, then run `python -m unittest -v test_api.py`. From `frontend/`, run `npm run lint` and `npm run build` after installing dependencies.

The 15 backend tests exercise every implemented API operation, CRUD for all nine required record types, private zones, tags, search/filtering, pagination, authentication, session and data persistence across application lifespans, JSON/BIND export, BIND import, bulk deletion, and selected validation/error paths. Import tests include shorthand owners, system-record inheritance, TTL units and combined units, TTL defaults and boundaries, and failures without partial writes.

Latest local verification: all 15 API integration tests pass. Frontend TypeScript checks (`npm run lint`) and the production build (`npm run build`) also pass. Browser interactions, exact visual similarity, and the hosted deployment still require separate verification. The BIND importer supports the documented subset above rather than the complete BIND grammar.

## Deployment

Repository name: **`GSK05/route53-console-clone`**. A single Docker host or platform with a persistent volume is the simplest deployment. Run the Compose setup behind HTTPS, set a non-default `DEMO_PASSWORD`, and persist `/data`. A deployment that discards its filesystem will also discard SQLite data, so an ephemeral backend host is unsuitable without an attached persistent disk.
