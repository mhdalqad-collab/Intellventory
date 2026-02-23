# IntellVentory — 3D Store Map (Vercel-ready)

Included:
- 3D store map UI (FEFO picker + layout editor)
- Login + roles:
  - ops user: user / 1234
  - admin: admin / 4321
- Storage:
  - If SUPABASE_URL and SUPABASE_SERVICE_KEY are set => Supabase (persistent, works on Vercel)
  - Otherwise => local CSV fallback (for local dev)

## Local run
1) pip install -r requirements.txt
2) python app.py
3) http://127.0.0.1:5000/login

## Vercel deploy (recommended + Supabase)
Set Env Vars in Vercel:
- SECRET_KEY
- SUPABASE_URL
- SUPABASE_SERVICE_KEY

Optional (override credentials):
- OPS_USER, OPS_PASS
- ADMIN_USER, ADMIN_PASS

## Three.js vendor files
Put these in `static/vendor/`:
- three.module.js
- OrbitControls.js
