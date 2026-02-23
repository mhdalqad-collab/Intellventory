from flask import Flask, render_template, request, jsonify, session, redirect, abort
import pandas as pd
import os, json
import requests
from functools import wraps

app = Flask(__name__)

# -----------------------------
# ✅ Session / Cookies (Fix login loop on Vercel)
# -----------------------------
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

IS_VERCEL = bool(os.environ.get("VERCEL"))

# On Vercel you are always HTTPS, so cookie must be Secure.
# If Secure cookie is used on HTTP locally, it won’t save; so we set it only on Vercel.
app.config.update(
    SESSION_COOKIE_SECURE=IS_VERCEL,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax" if not IS_VERCEL else "None",
    SESSION_COOKIE_PATH="/",
    SESSION_COOKIE_NAME="intellventory_session",
    PERMANENT_SESSION_LIFETIME=86400,  # 24 hours
)

# -----------------------------
# Users (env-based)
# -----------------------------
USERS = {
    os.environ.get("OPS_USER", "user"): {"password": os.environ.get("OPS_PASS", "1234"), "role": "ops"},
    os.environ.get("ADMIN_USER", "admin"): {"password": os.environ.get("ADMIN_PASS", "4321"), "role": "admin"},
}

# -----------------------------
# Auth decorators
# -----------------------------
def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("username"):
            return redirect("/login")
        return fn(*args, **kwargs)
    return wrapper

def role_required(role):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get("username"):
                return redirect("/login")
            if session.get("role") != role:
                return abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return deco

# -----------------------------
# Pages
# -----------------------------
@app.route("/")
def root():
    return redirect("/login")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html", error=None)

    u = (request.form.get("username") or "").strip()
    p = (request.form.get("password") or "")
    rec = USERS.get(u)
    if not rec or rec["password"] != p:
        return render_template("login.html", error="Invalid credentials"), 401

    session["username"] = u
    session["role"] = rec["role"]
    session.permanent = True  # Use PERMANENT_SESSION_LIFETIME

    # Optional but helps some environments: mark session as modified
    session.modified = True

    return redirect("/map3d")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/map3d")
@login_required
def map3d():
    return render_template("map3d.html")

# -----------------------------
# API: who am I
# -----------------------------
@app.route("/api/me")
@login_required
def api_me():
    return jsonify({"ok": True, "username": session.get("username"), "role": session.get("role", "ops")})

@app.route("/api/storage")
@login_required
def api_storage():
    """Returns which storage backend is active. Used by the UI to warn about ephemeral /tmp storage."""
    return jsonify({
        "backend": "supabase" if USE_SUPABASE else "csv_tmp",
        "persistent": USE_SUPABASE,
        "warning": None if USE_SUPABASE else (
            "⚠️ Running WITHOUT Supabase. Data is saved to /tmp which Vercel wipes between requests. "
            "Set SUPABASE_URL and SUPABASE_SERVICE_KEY in your Vercel environment variables."
        )
    })

# -----------------------------
# Storage selection: Supabase if configured, else CSV
# -----------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)

# Vercel serverless filesystem is read-only except /tmp
DATA_PATH = "/tmp/intellventory_data" if IS_VERCEL else "data"
os.makedirs(DATA_PATH, exist_ok=True)

MASTER_FILE   = os.path.join(DATA_PATH, "MasterItemList_template.csv")
STOCK_FILE    = os.path.join(DATA_PATH, "StockSnapshot_template.csv")
BINS_FILE     = os.path.join(DATA_PATH, "Bins_template.csv")
ENTRANCE_FILE = os.path.join(DATA_PATH, "entrance.json")

def safe_load_csv(path, cols=None):
    if not os.path.exists(path):
        return pd.DataFrame(columns=cols) if cols else pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=cols) if cols else pd.DataFrame()

def safe_write_csv(df: pd.DataFrame, path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    df.to_csv(path, index=False)

def ensure_templates():
    # NOTE: On Vercel, /tmp is writable, so this is safe.
    if not os.path.exists(MASTER_FILE):
        safe_write_csv(pd.DataFrame(columns=["SKU_ID","Item_Name","Category","Brand","Price"]), MASTER_FILE)
    if not os.path.exists(STOCK_FILE):
        safe_write_csv(pd.DataFrame(columns=["SKU_ID","Location_ID","Bin_ID","Quantity_On_Hand","Expiry_Date","Received_Date"]), STOCK_FILE)
    if not os.path.exists(BINS_FILE):
        safe_write_csv(pd.DataFrame(columns=["Bin_ID","Floor","X","Y","Z","Zone","Bin_Capacity_units","Temperature_Controlled","Display_Name"]), BINS_FILE)
    if not os.path.exists(ENTRANCE_FILE):
        with open(ENTRANCE_FILE, "w", encoding="utf-8") as f:
            json.dump({"x":0.0,"y":0.0,"z":0.0}, f)

if not USE_SUPABASE:
    ensure_templates()

def make_json_safe(records):
    safe = []
    for r in records:
        nr = {}
        for k, v in r.items():
            try:
                if pd.isna(v):
                    nr[k] = None
                    continue
            except Exception:
                pass

            if isinstance(v, (str, int, float, bool)):
                nr[k] = v
                continue

            try:
                nr[k] = pd.Timestamp(v).isoformat()
                continue
            except Exception:
                pass

            nr[k] = str(v)
        safe.append(nr)
    return safe

def _ensure_bins_schema(df: pd.DataFrame) -> pd.DataFrame:
    if "Display_Name" not in df.columns:
        df["Display_Name"] = ""
    if "Zone" not in df.columns:
        df["Zone"] = "FAST_MOVING"
    if "Bin_Capacity_units" not in df.columns:
        df["Bin_Capacity_units"] = 100
    if "Temperature_Controlled" not in df.columns:
        df["Temperature_Controlled"] = False
    if "Floor" not in df.columns:
        df["Floor"] = "G"
    return df

# Supabase REST helpers
def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

def sb_get(table, params=None):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    r = requests.get(url, headers=sb_headers(), params=params or {})
    r.raise_for_status()
    return r.json()

def sb_post(table, payload, prefer):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    h = sb_headers()
    h["Prefer"] = prefer
    r = requests.post(url, headers=h, data=json.dumps(payload))
    r.raise_for_status()
    return r.json() if r.text else None

def sb_patch(table, payload, match_params, prefer):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    h = sb_headers()
    h["Prefer"] = prefer
    r = requests.patch(url, headers=h, params=match_params, data=json.dumps(payload))
    r.raise_for_status()
    return r.json() if r.text else None

def sb_delete(table, match_params):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    r = requests.delete(url, headers=sb_headers(), params=match_params)
    r.raise_for_status()
    return True

def load_master():
    if USE_SUPABASE:
        rows = sb_get("products", {"select":"sku_id,item_name,category,brand,price"})
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.rename(columns={"sku_id":"SKU_ID","item_name":"Item_Name","category":"Category","brand":"Brand","price":"Price"})
        return df
    df = safe_load_csv(MASTER_FILE)
    if "SKU_ID" in df.columns:
        df["SKU_ID"] = df["SKU_ID"].astype(str)
    return df

def load_bins():
    if USE_SUPABASE:
        rows = sb_get("bins", {"select":"bin_id,floor,x,y,z,zone,bin_capacity_units,temperature_controlled,display_name"})
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.rename(columns={
                "bin_id":"Bin_ID","floor":"Floor","x":"X","y":"Y","z":"Z",
                "zone":"Zone","bin_capacity_units":"Bin_Capacity_units",
                "temperature_controlled":"Temperature_Controlled","display_name":"Display_Name"
            })
        return _ensure_bins_schema(df)
    df = safe_load_csv(BINS_FILE)
    for c in ["X","Y","Z"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return _ensure_bins_schema(df)

def load_stock():
    if USE_SUPABASE:
        rows = sb_get("stock_lots", {"select":"id,sku_id,bin_id,quantity_on_hand,expiry_date,received_date"})
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.rename(columns={
                "id":"Lot_ID","sku_id":"SKU_ID","bin_id":"Bin_ID","quantity_on_hand":"Quantity_On_Hand",
                "expiry_date":"Expiry_Date","received_date":"Received_Date"
            })
        for col in ["Expiry_Date","Received_Date"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        return df
    df = safe_load_csv(STOCK_FILE)
    for col in ["Expiry_Date","Received_Date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df

def load_entrance():
    if USE_SUPABASE:
        rows = sb_get("entrance", {"select":"id,x,y,z","limit":"1"})
        if rows:
            r = rows[0]
            return {"x": float(r["x"]), "y": float(r["y"]), "z": float(r["z"])}
        return {"x":0.0,"y":0.0,"z":0.0}
    try:
        with open(ENTRANCE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"x":0.0,"y":0.0,"z":0.0}

def save_entrance(obj):
    if USE_SUPABASE:
        rows = sb_get("entrance", {"select":"id","limit":"1"})
        if rows:
            sb_patch("entrance", {"x":obj["x"],"y":obj["y"],"z":obj["z"]}, {"id":"eq."+str(rows[0]["id"])}, "return=minimal")
        else:
            sb_post("entrance", {"x":obj["x"],"y":obj["y"],"z":obj["z"]}, "return=minimal")
        return
    with open(ENTRANCE_FILE, "w", encoding="utf-8") as f:
        json.dump(obj, f)

def fefo_pick_recommendation(sku: str):
    inv = load_stock()
    if inv.empty:
        return {"error":"No inventory loaded"}
    sku = str(sku).strip()
    inv = inv[inv["SKU_ID"].astype(str) == sku].copy()
    if inv.empty:
        return {"error":"SKU not found in stock"}
    inv["Quantity_On_Hand"] = pd.to_numeric(inv.get("Quantity_On_Hand", 0), errors="coerce").fillna(0)
    inv = inv[inv["Quantity_On_Hand"] > 0].copy()

    inv["Expiry_Date"] = pd.to_datetime(inv.get("Expiry_Date"), errors="coerce")
    today = pd.Timestamp.now().normalize()
    expired = inv[(~inv["Expiry_Date"].isna()) & (inv["Expiry_Date"] < today)].copy()
    valid = inv[(inv["Expiry_Date"].isna()) | (inv["Expiry_Date"] >= today)].copy()

    if valid.empty:
        return {"sku": sku, "expired_only": True, "expired_bins": make_json_safe(expired[["Bin_ID","Expiry_Date","Quantity_On_Hand"]].to_dict("records"))}

    valid["Expiry_Sort"] = valid["Expiry_Date"].fillna(pd.Timestamp.max)
    valid = valid.sort_values(["Expiry_Sort","Bin_ID"])
    best = valid.iloc[0]
    return {
        "sku": sku,
        "pick_from": str(best.get("Bin_ID","")),
        "expiry": None if pd.isna(best.get("Expiry_Date")) else str(pd.Timestamp(best["Expiry_Date"]).date()),
        "quantity": int(best.get("Quantity_On_Hand", 0)),
        "all_locations": make_json_safe(valid[["Bin_ID","Expiry_Date","Quantity_On_Hand"]].to_dict("records")),
        "expired": make_json_safe(expired[["Bin_ID","Expiry_Date","Quantity_On_Hand"]].to_dict("records"))
    }

def get_item(sku: str):
    df = load_master()
    if df.empty: return None
    row = df[df["SKU_ID"].astype(str) == str(sku).strip()]
    if row.empty: return None
    return make_json_safe([row.iloc[0].to_dict()])[0]

def lots_in_bin(bin_id: str, sku: str):
    df = load_stock()
    if df.empty:
        return {"sku": sku, "bin": bin_id, "available": 0, "lots": []}

    df["Quantity_On_Hand"] = pd.to_numeric(df.get("Quantity_On_Hand", 0), errors="coerce").fillna(0)
    df["Expiry_Date"] = pd.to_datetime(df.get("Expiry_Date"), errors="coerce")
    df["Received_Date"] = pd.to_datetime(df.get("Received_Date"), errors="coerce")

    sub = df[(df["SKU_ID"].astype(str) == str(sku)) & (df["Bin_ID"].astype(str) == str(bin_id)) & (df["Quantity_On_Hand"] > 0)].copy()
    if sub.empty:
        return {"sku": sku, "bin": bin_id, "available": 0, "lots": []}

    sub["Expiry_Sort"] = sub["Expiry_Date"].fillna(pd.Timestamp.max)
    sub = sub.sort_values(["Expiry_Sort","Received_Date"])

    lots = []
    for _, r in sub.iterrows():
        lots.append({
            "Quantity_On_Hand": int(r.get("Quantity_On_Hand", 0)),
            "Expiry_Date": None if pd.isna(r.get("Expiry_Date")) else str(pd.Timestamp(r["Expiry_Date"]).date()),
            "Received_Date": None if pd.isna(r.get("Received_Date")) else str(pd.Timestamp(r["Received_Date"]).date())
        })
    available = int(sub["Quantity_On_Hand"].sum())
    return {"sku": sku, "bin": bin_id, "available": available, "lots": lots}

def take_from_bin(sku: str, bin_id: str, qty: int):
    qty = int(qty)
    if qty <= 0:
        return {"ok": False, "error":"Quantity must be > 0"}

    if USE_SUPABASE:
        rows = sb_get("stock_lots", {
            "select":"id,quantity_on_hand,expiry_date,received_date",
            "sku_id":"eq."+sku,
            "bin_id":"eq."+bin_id,
            "quantity_on_hand":"gt.0",
            "order":"expiry_date.asc.nullslast,received_date.asc"
        })
        available = sum(int(r["quantity_on_hand"]) for r in rows) if rows else 0
        if available <= 0:
            return {"ok": False, "error":"No stock available in this bin", "available": 0}
        if qty > available:
            return {"ok": False, "error":"Requested quantity exceeds available stock", "available": available}

        remaining = qty
        for r in rows:
            if remaining <= 0: break
            have = int(r["quantity_on_hand"])
            take = min(have, remaining)
            remaining -= take
            sb_patch("stock_lots", {"quantity_on_hand": have - take}, {"id":"eq."+str(r["id"])}, "return=minimal")
        sb_delete("stock_lots", {"sku_id":"eq."+sku, "bin_id":"eq."+bin_id, "quantity_on_hand":"eq.0"})
        return {"ok": True, "taken": qty, "available_before": available, "available_after": available-qty}

    df = load_stock()
    df["Quantity_On_Hand"] = pd.to_numeric(df.get("Quantity_On_Hand", 0), errors="coerce").fillna(0)
    df["Expiry_Date"] = pd.to_datetime(df.get("Expiry_Date"), errors="coerce")
    df["Received_Date"] = pd.to_datetime(df.get("Received_Date"), errors="coerce")

    mask = (df["SKU_ID"].astype(str) == sku) & (df["Bin_ID"].astype(str) == bin_id) & (df["Quantity_On_Hand"] > 0)
    sub = df[mask].copy()
    available = int(sub["Quantity_On_Hand"].sum()) if not sub.empty else 0
    if available <= 0:
        return {"ok": False, "error":"No stock available in this bin", "available": 0}
    if qty > available:
        return {"ok": False, "error":"Requested quantity exceeds available stock", "available": available}

    sub["Expiry_Sort"] = sub["Expiry_Date"].fillna(pd.Timestamp.max)
    sub = sub.sort_values(["Expiry_Sort","Received_Date"])

    remaining = qty
    for idx, r in sub.iterrows():
        if remaining <= 0: break
        take = min(int(r["Quantity_On_Hand"]), remaining)
        remaining -= take
        df.loc[idx, "Quantity_On_Hand"] = int(r["Quantity_On_Hand"]) - take

    df = df[df["Quantity_On_Hand"] > 0].copy()
    safe_write_csv(df, STOCK_FILE)
    return {"ok": True, "taken": qty, "available_before": available, "available_after": available-qty}

# -----------------------------
# API routes
# -----------------------------
@app.route("/api/products")
@login_required
def api_products():
    df = load_master()
    if df.empty or "SKU_ID" not in df.columns:
        return jsonify([])
    cols = [c for c in ["SKU_ID","Item_Name","Category"] if c in df.columns]
    out = df[cols].copy()
    out["SKU_ID"] = out["SKU_ID"].astype(str)
    return jsonify(make_json_safe(out.to_dict("records")))

@app.route("/api/item/<sku>")
@login_required
def api_item(sku):
    rec = get_item(str(sku).strip())
    if not rec:
        return jsonify({"error":"SKU not found"}), 404
    return jsonify(rec)

@app.route("/api/bins")
@login_required
def api_bins():
    df = load_bins()
    for c in ["X","Y","Z"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=[c for c in ["X","Y","Z"] if c in df.columns])
    return jsonify(make_json_safe(df.to_dict(orient="records")))

@app.route("/api/pick_sku/<sku>")
@login_required
def api_pick_sku(sku):
    return jsonify(fefo_pick_recommendation(sku))

@app.route("/api/entrance", methods=["GET"])
@login_required
def api_get_entrance():
    return jsonify(load_entrance())

@app.route("/api/entrance", methods=["POST"])
@role_required("admin")
def api_set_entrance():
    payload = request.get_json(silent=True) or {}
    x = float(payload.get("x", 0)); y = float(payload.get("y", 0)); z = float(payload.get("z", 0))
    save_entrance({"x": x, "y": y, "z": z})
    return jsonify({"ok": True})

@app.route("/api/stock/bin_sku")
@login_required
def api_stock_bin_sku():
    sku = str(request.args.get("sku", "")).strip()
    bin_id = str(request.args.get("bin", "") or request.args.get("bin_id","")).strip()
    if not sku or not bin_id:
        return jsonify({"error":"Missing sku or bin"}), 400
    return jsonify(lots_in_bin(bin_id, sku))

@app.route("/api/stock/take_from_bin", methods=["POST"])
@login_required
def api_take_from_bin():
    payload = request.get_json(silent=True) or {}
    sku = str(payload.get("SKU_ID", "")).strip()
    bin_id = str(payload.get("Bin_ID", "")).strip()
    qty = int(payload.get("Quantity", 0) or 0)
    if not sku or not bin_id or qty <= 0:
        return jsonify({"ok": False, "error":"Missing SKU_ID, Bin_ID, or Quantity"}), 400
    result = take_from_bin(sku, bin_id, qty)
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result)

@app.route("/api/bins/save_all", methods=["POST"])
@role_required("admin")
def api_bins_save_all():
    payload = request.get_json(silent=True) or {}
    bins_list = payload.get("bins", [])
    deleted_ids = payload.get("deleted_bin_ids", []) or []
    if not isinstance(bins_list, list):
        return jsonify({"ok": False, "error":"bins must be a list"}), 400
    if not isinstance(deleted_ids, list):
        return jsonify({"ok": False, "error":"deleted_bin_ids must be a list"}), 400

    if USE_SUPABASE:
        for bid in deleted_ids:
            sb_delete("bins", {"bin_id":"eq."+str(bid)})

        url = f"{SUPABASE_URL}/rest/v1/bins"
        headers = sb_headers()
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
        rows = []
        for b in bins_list:
            rows.append({
                "bin_id": str(b.get("Bin_ID","")),
                "floor": str(b.get("Floor","G")),
                "x": float(b.get("X",0) or 0),
                "y": float(b.get("Y",0) or 0),
                "z": float(b.get("Z",0) or 0),
                "zone": str(b.get("Zone","FAST_MOVING")),
                "bin_capacity_units": int(b.get("Bin_Capacity_units",100) or 100),
                "temperature_controlled": bool(b.get("Temperature_Controlled", False)),
                "display_name": str(b.get("Display_Name","") or "")
            })
        r = requests.post(url, headers=headers, data=json.dumps(rows))
        if not r.ok:
            return jsonify({"ok": False, "error":"Supabase upsert failed", "detail": r.text}), 400
        return jsonify({"ok": True, "bins_written": len(rows), "deleted": len(deleted_ids), "storage":"supabase"})

    df = pd.DataFrame(bins_list)
    if df.empty:
        df = pd.DataFrame(columns=["Bin_ID","Floor","X","Y","Z","Zone","Bin_Capacity_units","Temperature_Controlled","Display_Name"])
    keep = ["Bin_ID","Floor","X","Y","Z","Zone","Bin_Capacity_units","Temperature_Controlled","Display_Name"]
    for c in keep:
        if c not in df.columns:
            df[c] = "" if c in ["Bin_ID","Floor","Zone","Display_Name"] else 0
    df = df[keep].copy()
    safe_write_csv(df, BINS_FILE)
    return jsonify({"ok": True, "bins_written": int(len(df)), "deleted": int(len(deleted_ids)), "storage":"csv"})

@app.errorhandler(403)
def forbidden(_):
    return jsonify({"ok": False, "error":"Forbidden"}), 403

if __name__ == "__main__":
    app.run(debug=True, port=5000)
