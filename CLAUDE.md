# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

IntellVentory is a 3D warehouse/store inventory management app with FEFO (First Expired, First Out) picking and an interactive layout editor. Flask backend + Three.js frontend, deployed on Vercel as a serverless function.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (serves on http://127.0.0.1:5000)
python app.py

# Deploy (all routes go through api/index.py on Vercel)
vercel deploy
```

There are no tests or linting configured.

## Architecture

**Single-file backend:** `app.py` contains all Flask routes, auth decorators, data access, and business logic. `api/index.py` is a thin Vercel wrapper that imports the Flask app.

**Single-page frontend:** `templates/map3d.html` is a large monolithic file (~1300 lines) containing all JavaScript (Three.js scene, UI logic, API calls, layout editor) and CSS inline. `templates/login.html` is the login form.

**Dual storage backend:** If `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` env vars are set, the app uses Supabase (PostgreSQL via REST API). Otherwise, it falls back to CSV files in `data/` (or `/tmp/intellventory_data` on Vercel). The schema is in `supabase_schema.sql`.

**Auth:** Flask session cookies with two roles — `ops` (view/pick only) and `admin` (full access including layout editing). Credentials come from env vars with defaults: `user/1234` and `admin/4321`. Protected with `@login_required` and `@role_required("admin")` decorators.

## Key Data Flow

1. Login POST → session cookie set → redirect to `/map3d`
2. `map3d.html` boots by calling `/api/me` to verify session and get role
3. Frontend loads bins (`/api/bins`) and products (`/api/products`) to render the 3D scene
4. FEFO picking: `/api/pick_sku/<sku>` returns sorted locations by expiry date
5. Stock deduction: `/api/stock/take_from_bin` reduces quantity in FEFO order

## Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `SECRET_KEY` | Vercel | Flask session signing key |
| `SUPABASE_URL` | For persistence | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | For persistence | Supabase service role key |
| `OPS_USER` / `OPS_PASS` | No | Override default ops credentials |
| `ADMIN_USER` / `ADMIN_PASS` | No | Override default admin credentials |

The `VERCEL` env var (set automatically by Vercel) controls session cookie flags (`Secure`, `SameSite=None`) and storage path (`/tmp`).

## Conventions

- API responses use `{"ok": true/false, ...}` pattern for mutation endpoints
- `make_json_safe()` converts pandas objects to JSON-serializable types before returning
- Bin IDs follow the format `{Floor}-{Line}-{Slot}-{Shelf}` (e.g., `G-A-7-B`)
- Frontend uses `apiFetch()` wrapper that includes credentials and detects HTML responses (session expiry)
