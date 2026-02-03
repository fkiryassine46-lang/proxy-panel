#!/usr/bin/env python3
import os
import json
import datetime
from functools import wraps

import requests
from flask import (
    Flask,
    request,
    redirect,
    url_for,
    render_template_string,
    session,
    send_file,
    jsonify,
    abort,
)
from werkzeug.security import generate_password_hash, check_password_hash
from io import BytesIO

# -----------------------------
# Configuration de base
# -----------------------------

APP_TITLE = "Proxy Panel"

PROXY_SOURCE_FILE = "/root/proxies.txt"
DB_FILE = "/root/proxy_panel_db.json"
CONFIG_FILE = "/root/proxy_panel_config.json"

CHECK_TEST_URL = "https://www.google.com"
CHECK_TIMEOUT = 20  # secondes

app = Flask(__name__)
app.secret_key = "change-me-if-you-want"  # pour la session Flask


# =============================
# Utilitaires fichiers
# =============================

def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


# =============================
# Config admin (login / pass)
# =============================

def ensure_config():
    cfg = load_json(CONFIG_FILE, {})
    changed = False
    if "admin_username" not in cfg:
        cfg["admin_username"] = "admin"
        changed = True
    if "password_hash" not in cfg:
        cfg["password_hash"] = generate_password_hash("lolopolo")
        changed = True
    if changed:
        save_json(CONFIG_FILE, cfg)
    return cfg


def get_config():
    return ensure_config()


def set_admin_password(new_username, new_password):
    cfg = get_config()
    if new_username:
        cfg["admin_username"] = new_username
    if new_password:
        cfg["password_hash"] = generate_password_hash(new_password)
    save_json(CONFIG_FILE, cfg)


# =============================
# DB clients / proxies
# =============================

def ensure_db():
    db = load_json(DB_FILE, {})
    changed = False
    if "clients" not in db:
        db["clients"] = []
        changed = True
    if "assigned_proxies" not in db:
        db["assigned_proxies"] = {}  # proxy_line -> client_id
        changed = True
    if changed:
        save_json(DB_FILE, db)
    return db


def get_db():
    return ensure_db()


def save_db(db):
    save_json(DB_FILE, db)


def next_client_id(db):
    if not db["clients"]:
        return 1
    return max(c["id"] for c in db["clients"]) + 1


# =============================
# Proxies: chargement + stats
# =============================

def load_proxy_list():
    if not os.path.exists(PROXY_SOURCE_FILE):
        return []
    proxies = []
    with open(PROXY_SOURCE_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            proxies.append(line)
    return proxies


def compute_stats():
    db = get_db()
    all_proxies = load_proxy_list()
    assigned = set(db["assigned_proxies"].keys())
    total = len(all_proxies)
    assigned_count = len(assigned & set(all_proxies))
    available_count = total - assigned_count
    clients_count = len(db["clients"])
    return {
        "total_proxies": total,
        "assigned_proxies": assigned_count,
        "available_proxies": available_count,
        "clients_count": clients_count,
        "proxy_source": PROXY_SOURCE_FILE,
        "db_file": DB_FILE,
    }


# =============================
# Login / sessions
# =============================

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


# =============================
# Parsing / check des proxies
# =============================

def parse_proxy_line(line: str):
    """
    Accepte :
      IP:PORT
      IP:PORT:USER:PASS
    Retourne dict ou None si invalide.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split(":")
    if len(parts) == 2:
        host, port = parts
        user = password = None
    elif len(parts) == 4:
        host, port, user, password = parts
    else:
        return None
    try:
        port = int(port)
    except ValueError:
        return None
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
    }


def build_proxy_url(info: dict) -> str:
    if info["user"] and info["password"]:
        return f"http://{info['user']}:{info['password']}@{info['host']}:{info['port']}"
    else:
        return f"http://{info['host']}:{info['port']}"


def check_proxy(proxy_line: str, timeout: float = CHECK_TIMEOUT) -> bool:
    """
    Test via HTTPS sur Google.
    True = OK, False = FAIL.
    """
    info = parse_proxy_line(proxy_line)
    if not info:
        return False

    proxy_url = build_proxy_url(info)
    proxies = {
        "http": proxy_url,
        "https": proxy_url,
    }

    try:
        r = requests.get(CHECK_TEST_URL, proxies=proxies, timeout=timeout)
        return 200 <= r.status_code < 400
    except Exception:
        return False


# =============================
# Templates
# =============================

LOGIN_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ title }} - Login</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    body {
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(circle at top, #1f2a3c, #050816);
      color: #f8fafc;
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100vh;
    }
    .card {
      background: linear-gradient(135deg, #141b2b, #050816);
      border-radius: 18px;
      padding: 32px 40px;
      width: 360px;
      box-shadow: 0 18px 40px rgba(0,0,0,0.6);
      position: relative;
    }
    .dot {
      width: 9px; height: 9px;
      border-radius: 50%;
      background: #ec4899;
      box-shadow: 0 0 12px rgba(236,72,153,0.6);
      display: inline-block;
      margin-right: 8px;
    }
    h1 {
      font-size: 22px;
      margin: 0 0 4px;
    }
    .subtitle {
      font-size: 12px;
      color: #9ca3af;
      margin-bottom: 20px;
    }
    label {
      font-size: 13px;
      color: #e5e7eb;
      display: block;
      margin-bottom: 6px;
    }
    input {
      width: 100%;
      padding: 9px 11px;
      border-radius: 10px;
      border: 1px solid #1f2937;
      background: rgba(15,23,42,0.9);
      color: #e5e7eb;
      font-size: 14px;
      outline: none;
      box-sizing: border-box;
    }
    input:focus {
      border-color: #6366f1;
      box-shadow: 0 0 0 1px rgba(99,102,241,0.4);
    }
    .field {
      margin-bottom: 16px;
    }
    .btn {
      margin-top: 6px;
      width: 100%;
      padding: 9px;
      border-radius: 999px;
      border: none;
      background: linear-gradient(135deg, #6366f1, #ec4899);
      color: #f9fafb;
      cursor: pointer;
      font-weight: 600;
      font-size: 14px;
    }
    .btn:hover {
      filter: brightness(1.05);
    }
    .error {
      margin-top: 10px;
      font-size: 13px;
      color: #fca5a5;
    }
  </style>
</head>
<body>
  <div class="card">
    <div style="display:flex;align-items:center;margin-bottom:10px;">
      <span class="dot"></span>
      <span style="font-size:12px;color:#9ca3af;">Local admin login</span>
    </div>
    <h1>{{ title }}</h1>
    <div class="subtitle">Sign in to manage your proxy pool.</div>

    <form method="post">
      <div class="field">
        <label>Username</label>
        <input type="text" name="username" value="{{ default_user }}">
      </div>
      <div class="field">
        <label>Password</label>
        <input type="password" name="password">
      </div>
      <button class="btn" type="submit">Sign in</button>
    </form>

    {% if error %}
      <div class="error">{{ error }}</div>
    {% endif %}
  </div>
</body>
</html>
"""

LAYOUT_TEMPLATE = """
{% macro nav_link(href, label, active_name) -%}
  <a href="{{ href }}" class="nav-link {{ 'active' if active == active_name else '' }}">{{ label }}</a>
{%- endmacro %}

<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ title }} - {{ page_title }}</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root {
      --bg-main: #020617;
      --text-main: #f9fafb;
      --text-muted: #9ca3af;
      --accent-1: #6366f1;
      --accent-2: #ec4899;
      --accent-bad: #ef4444;
      --accent-ok: #22c55e;
      --accent-warn: #f59e0b;
    }
    * { box-sizing:border-box; }
    body {
      margin:0;
      font-family: system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      background: radial-gradient(circle at top, #111827, #020617);
      color: var(--text-main);
      min-height:100vh;
    }
    .shell {
      max-width: 1280px;
      margin: 0 auto;
      padding: 18px 20px 32px;
    }
    header {
      display:flex;
      align-items:center;
      justify-content:space-between;
      margin-bottom:18px;
    }
    .logo {
      font-weight:700;
      letter-spacing:0.08em;
      font-size:14px;
      text-transform:uppercase;
    }
    .logo span {
      background: linear-gradient(135deg, var(--accent-1), var(--accent-2));
      -webkit-background-clip:text;
      color:transparent;
    }
    nav {
      display:flex;
      gap:12px;
      align-items:center;
    }
    .nav-link {
      font-size:13px;
      padding:6px 12px;
      border-radius:999px;
      color:var(--text-muted);
      text-decoration:none;
      border:1px solid transparent;
      background: rgba(15,23,42,0.8);
    }
    .nav-link:hover {
      color:var(--text-main);
      border-color:rgba(148,163,184,0.4);
    }
    .nav-link.active {
      color:#f9fafb;
      background:linear-gradient(135deg,var(--accent-1),var(--accent-2));
      border-color:transparent;
    }
    .logout-link {
      font-size:12px;
      color:var(--text-muted);
      text-decoration:none;
      padding:6px 10px;
      border-radius:999px;
      border:1px solid rgba(148,163,184,0.4);
      background:rgba(15,23,42,0.8);
    }
    .logout-link:hover { color:#fee2e2; border-color:#fca5a5; }

    h2 {
      margin:0 0 14px;
      font-size:19px;
    }
    .grid {
      display:grid;
      grid-template-columns: repeat(auto-fit,minmax(230px,1fr));
      gap:14px;
      margin-bottom:18px;
    }
    .card {
      background: radial-gradient(circle at top left, #020617, #020617);
      border-radius:18px;
      padding:16px 18px;
      border:1px solid rgba(148,163,184,0.25);
      box-shadow:0 18px 40px rgba(15,23,42,0.9);
    }
    .card h3 {
      margin:0 0 6px;
      font-size:14px;
      color:#e5e7eb;
    }
    .card .big {
      font-size:28px;
      font-weight:600;
    }
    .muted {
      font-size:12px;
      color:var(--text-muted);
    }
    .pill {
      display:inline-block;
      font-size:11px;
      padding:3px 8px;
      border-radius:999px;
      border:1px solid rgba(148,163,184,0.4);
      color:var(--text-muted);
    }

    table {
      width:100%;
      border-collapse:collapse;
      font-size:13px;
    }
    th, td {
      padding:8px 10px;
      text-align:left;
      border-bottom:1px solid rgba(15,23,42,0.9);
    }
    th {
      font-size:12px;
      text-transform:uppercase;
      letter-spacing:0.06em;
      color:var(--text-muted);
    }
    tr:hover td {
      background:rgba(15,23,42,0.5);
    }

    .btn {
      display:inline-flex;
      align-items:center;
      justify-content:center;
      padding:7px 14px;
      border-radius:999px;
      border:none;
      cursor:pointer;
      font-size:13px;
      color:#f9fafb;
      background:linear-gradient(135deg,var(--accent-1),var(--accent-2));
    }
    .btn:hover { filter:brightness(1.05); }
    .btn-secondary {
      background:transparent;
      border:1px solid rgba(148,163,184,0.5);
      color:var(--text-muted);
    }
    .btn-secondary:hover {
      border-color:var(--accent-1);
      color:#e5e7eb;
    }

    .status-badge {
      padding:3px 9px;
      border-radius:999px;
      font-size:11px;
      font-weight:500;
    }
    .status-ok {
      background:rgba(34,197,94,0.12);
      color:#4ade80;
      border:1px solid rgba(34,197,94,0.4);
    }
    .status-fail {
      background:rgba(239,68,68,0.12);
      color:#fca5a5;
      border:1px solid rgba(239,68,68,0.4);
    }
    .status-unknown {
      background:rgba(148,163,184,0.12);
      color:#e5e7eb;
      border:1px solid rgba(148,163,184,0.4);
    }
    .status-checking {
      background:rgba(245,158,11,0.15);
      color:#fbbf24;
      border:1px solid rgba(245,158,11,0.5);
    }

    .form-row {
      display:flex;
      flex-wrap:wrap;
      gap:12px;
      margin-bottom:12px;
    }
    .form-row label {
      font-size:12px;
      color:var(--text-muted);
      display:block;
      margin-bottom:3px;
    }
    .form-row input {
      border-radius:10px;
      border:1px solid rgba(51,65,85,0.9);
      background:rgba(15,23,42,0.9);
      color:#e5e7eb;
      padding:7px 9px;
      min-width:140px;
      font-size:13px;
    }
    .form-row input:focus {
      outline:none;
      border-color:var(--accent-1);
      box-shadow:0 0 0 1px rgba(99,102,241,0.4);
    }

    .error-msg {
      color:#fca5a5;
      font-size:12px;
      margin-top:4px;
    }

    .footer-note {
      margin-top:18px;
      font-size:11px;
      color:var(--text-muted);
      text-align:center;
    }

    @media (max-width: 680px) {
      header { flex-direction:column; align-items:flex-start; gap:10px; }
      .shell { padding:16px 14px 28px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div class="logo"><span>{{ title }}</span></div>
      <nav>
        {{ nav_link(url_for('dashboard'), 'Dashboard', 'dashboard') }}
        {{ nav_link(url_for('clients'), 'Clients', 'clients') }}
        {{ nav_link(url_for('proxies'), 'Proxies', 'proxies') }}
        {{ nav_link(url_for('settings'), 'Settings', 'settings') }}
        <a class="logout-link" href="{{ url_for('logout') }}">Logout</a>
      </nav>
    </header>

    {{ body|safe }}

    <div class="footer-note">
      Local-only admin panel. Proxy source: {{ stats.proxy_source }} · DB: {{ stats.db_file }}
    </div>
  </div>
</body>
</html>
"""


# =============================
# Routes
# =============================

@app.route("/login", methods=["GET", "POST"])
def login():
    cfg = get_config()
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == cfg["admin_username"] and check_password_hash(cfg["password_hash"], password):
            session["logged_in"] = True
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
        else:
            error = "Invalid username or password."
    return render_template_string(
        LOGIN_TEMPLATE,
        title=APP_TITLE,
        error=error,
        default_user=cfg["admin_username"],
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def root():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
@login_required
def dashboard():
    stats = compute_stats()
    body = render_template_string("""
      <h2>Overview</h2>
      <div class="grid">
        <div class="card">
          <h3>Proxies</h3>
          <div class="big">{{ stats.total_proxies }}</div>
          <div class="muted">Total proxies in pool</div>
          <div class="muted" style="margin-top:6px;">
            <span style="color:#4ade80;">Available: {{ stats.available_proxies }}</span>
            &nbsp;·&nbsp;
            <span style="color:#fca5a5;">Assigned: {{ stats.assigned_proxies }}</span>
          </div>
        </div>
        <div class="card">
          <h3>Clients</h3>
          <div class="big">{{ stats.clients_count }}</div>
          <div class="muted">Each client has its own txt file.</div>
        </div>
        <div class="card">
          <h3>Pool status</h3>
          <div class="muted">Master proxy file:<br><code>{{ stats.proxy_source }}</code></div>
          <div class="muted" style="margin-top:6px;">Database file:<br><code>{{ stats.db_file }}</code></div>
        </div>
      </div>
    """, stats=stats)
    return render_template_string(
        LAYOUT_TEMPLATE,
        title=APP_TITLE,
        page_title="Dashboard",
        body=body,
        active="dashboard",
        stats=stats,
    )


@app.route("/clients", methods=["GET", "POST"])
@login_required
def clients():
    stats = compute_stats()
    db = get_db()
    error = None

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        count_str = request.form.get("count", "").strip()
        try:
            count = int(count_str)
        except ValueError:
            count = 0

        if not name:
            error = "Client name is required."
        elif count <= 0:
            error = "Number of proxies must be a positive integer."
        else:
            all_proxies = load_proxy_list()
            assigned = set(db["assigned_proxies"].keys())
            available = [p for p in all_proxies if p not in assigned]

            if len(available) < count:
                error = f"Not enough available proxies. Requested {count}, only {len(available)} left."
            else:
                selected = available[:count]
                client_id = next_client_id(db)
                created_at = datetime.datetime.now().isoformat(timespec="seconds")
                client = {
                    "id": client_id,
                    "name": name,
                    "count": count,
                    "proxies": selected,
                    "created_at": created_at,
                }
                db["clients"].append(client)
                for p in selected:
                    db["assigned_proxies"][p] = client_id
                save_db(db)

                # Préparer le fichier txt immédiatement
                filename = f"{name}_{count}proxies.txt"
                content = "\n".join(selected) + "\n"
                mem = BytesIO(content.encode("utf-8"))
                mem.seek(0)
                return send_file(
                    mem,
                    as_attachment=True,
                    download_name=filename,
                    mimetype="text/plain",
                )

    db = get_db()
    clients_list = sorted(db["clients"], key=lambda c: c["id"])

    body = render_template_string("""
      <h2>Clients</h2>
      <div class="grid">
        <div class="card">
          <h3>Create new client</h3>
          <form method="post">
            <div class="form-row">
              <div>
                <label>Client name</label>
                <input type="text" name="name" placeholder="e.g. Mohamed">
              </div>
              <div>
                <label>Number of proxies</label>
                <input type="number" name="count" min="1" placeholder="10">
              </div>
            </div>
            {% if error %}
              <div class="error-msg">{{ error }}</div>
            {% endif %}
            <button class="btn" type="submit">Create & download .txt</button>
            <div class="muted" style="margin-top:8px;">
              Available proxies: <strong>{{ stats.available_proxies }}</strong>
            </div>
          </form>
        </div>

        <div class="card">
          <h3>Summary</h3>
          <div class="muted">
            Total clients: <strong>{{ stats.clients_count }}</strong><br>
            Total proxies: <strong>{{ stats.total_proxies }}</strong><br>
            Assigned: <span style="color:#fca5a5;">{{ stats.assigned_proxies }}</span><br>
            Available: <span style="color:#4ade80;">{{ stats.available_proxies }}</span>
          </div>
        </div>
      </div>

      <div class="card" style="margin-top:16px;">
        <h3>Existing clients</h3>
        {% if not clients %}
          <div class="muted">No clients yet.</div>
        {% else %}
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Proxies</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {% for c in clients %}
                <tr>
                  <td>#{{ c.id }}</td>
                  <td>{{ c.name }}</td>
                  <td>{{ c.count }}</td>
                  <td>{{ c.created_at }}</td>
                  <td>
                    <a class="btn-secondary" href="{{ url_for('download_client', client_id=c.id) }}">Download</a>
                    <form method="post" action="{{ url_for('delete_client', client_id=c.id) }}" style="display:inline;" onsubmit="return confirm('Delete this client and free its proxies?');">
                      <button class="btn-secondary" type="submit" style="margin-left:6px;">Delete</button>
                    </form>
                  </td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        {% endif %}
      </div>
    """, stats=stats, clients=clients_list, error=error)
    return render_template_string(
        LAYOUT_TEMPLATE,
        title=APP_TITLE,
        page_title="Clients",
        body=body,
        active="clients",
        stats=stats,
    )


@app.route("/clients/<int:client_id>/download")
@login_required
def download_client(client_id):
    db = get_db()
    client = next((c for c in db["clients"] if c["id"] == client_id), None)
    if not client:
        abort(404)
    filename = f"{client['name']}_{client['count']}proxies.txt"
    content = "\n".join(client["proxies"]) + "\n"
    mem = BytesIO(content.encode("utf-8"))
    mem.seek(0)
    return send_file(
        mem,
        as_attachment=True,
        download_name=filename,
        mimetype="text/plain",
    )


@app.route("/clients/<int:client_id>/delete", methods=["POST"])
@login_required
def delete_client(client_id):
    db = get_db()
    new_clients = []
    removed_proxies = []
    for c in db["clients"]:
        if c["id"] == client_id:
            removed_proxies.extend(c.get("proxies", []))
        else:
            new_clients.append(c)
    db["clients"] = new_clients
    for p in removed_proxies:
        db["assigned_proxies"].pop(p, None)
    save_db(db)
    return redirect(url_for("clients"))


@app.route("/proxies")
@login_required
def proxies():
    stats = compute_stats()
    all_proxies = load_proxy_list()
    db = get_db()
    assigned_map = db["assigned_proxies"]

    table_data = []
    for p in all_proxies:
        client_id = assigned_map.get(p)
        table_data.append({
            "proxy": p,
            "client_id": client_id,
        })

    body = render_template_string("""
      <h2>Proxies</h2>
      <div class="card" style="margin-bottom:16px;">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;">
          <div class="muted">
            Total: <strong>{{ stats.total_proxies }}</strong> ·
            Available: <span style="color:#4ade80;">{{ stats.available_proxies }}</span> ·
            Assigned: <span style="color:#fca5a5;">{{ stats.assigned_proxies }}</span>
          </div>
          <button class="btn" id="check-all-btn">Check ALL Proxies</button>
        </div>
        <div class="muted" style="margin-top:6px;font-size:11px;">
          Performs an HTTPS request to Google using each proxy (timeout {{ timeout }}s).
        </div>
        <div id="check-summary" class="muted" style="margin-top:8px;font-size:12px;display:none;">
          Last check:
          <span id="sum-ok" style="color:#4ade80;">0 OK</span>
          ·
          <span id="sum-fail" style="color:#fca5a5;">0 Failed</span>
        </div>
      </div>

      <div class="card" style="max-height:480px;overflow:auto;">
        <table id="proxy-table">
          <thead>
            <tr>
              <th>Proxy</th>
              <th>Assigned to</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {% for row in rows %}
              <tr data-proxy="{{ row.proxy }}">
                <td class="proxy-cell"><code>{{ row.proxy }}</code></td>
                <td>
                  {% if row.client_id %}
                    <span class="pill">Client #{{ row.client_id }}</span>
                  {% else %}
                    <span class="muted">Unassigned</span>
                  {% endif %}
                </td>
                <td class="status-cell">
                  <span class="status-badge status-unknown">UNKNOWN</span>
                </td>
              </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>

      <script>
        const checkBtn = document.getElementById('check-all-btn');
        const table = document.getElementById('proxy-table');
        const summaryDiv = document.getElementById('check-summary');
        const sumOkSpan = document.getElementById('sum-ok');
        const sumFailSpan = document.getElementById('sum-fail');

        function setStatus(proxy, status) {
          const row = table.querySelector('tr[data-proxy="' + proxy.replace(/"/g,'&quot;') + '"]');
          if (!row) return;
          const cell = row.querySelector('.status-cell');
          if (!cell) return;

          let label = '';
          let cls = 'status-badge ';

          if (status === 'checking') {
            label = 'CHECKING';
            cls += 'status-checking';
          } else if (status === 'ok') {
            label = 'STATUS OK';
            cls += 'status-ok';
          } else if (status === 'fail') {
            label = 'STATUS FAIL';
            cls += 'status-fail';
          } else {
            label = 'UNKNOWN';
            cls += 'status-unknown';
          }
          cell.innerHTML = '<span class="' + cls + '">' + label + '</span>';
        }

        if (checkBtn) {
          checkBtn.addEventListener('click', () => {
            const rows = table.querySelectorAll('tbody tr');
            rows.forEach(row => {
              const proxy = row.getAttribute('data-proxy');
              setStatus(proxy, 'checking');
            });

            fetch('{{ url_for("proxies_check_all") }}', {
              method: 'POST',
              headers: {'X-Requested-With': 'XMLHttpRequest'}
            })
              .then(r => r.json())
              .then(data => {
                if (!data || !data.results) return;

                let okCount = 0;
                let failCount = 0;

                data.results.forEach(item => {
                  if (item.status === 'ok') {
                    okCount += 1;
                    setStatus(item.proxy, 'ok');
                  } else {
                    failCount += 1;
                    setStatus(item.proxy, 'fail');
                  }
                });

                if (summaryDiv) {
                  sumOkSpan.textContent = okCount + ' OK';
                  sumFailSpan.textContent = failCount + ' Failed';
                  summaryDiv.style.display = 'block';
                }
              })
              .catch(err => {
                console.error(err);
              });
          });
        }
      </script>
    """, stats=stats, rows=table_data, timeout=CHECK_TIMEOUT)
    return render_template_string(
        LAYOUT_TEMPLATE,
        title=APP_TITLE,
        page_title="Proxies",
        body=body,
        active="proxies",
        stats=stats,
    )


@app.route("/proxies/check-all", methods=["POST"])
@login_required
def proxies_check_all():
    proxies = load_proxy_list()
    results = []
    for p in proxies:
        ok = check_proxy(p)
        results.append({"proxy": p, "status": "ok" if ok else "fail"})
    return jsonify({"results": results})


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    stats = compute_stats()
    cfg = get_config()
    msg = None
    error = None

    if request.method == "POST":
        new_user = request.form.get("username", "").strip()
        new_pass = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if new_pass and new_pass != confirm:
            error = "Password confirmation does not match."
        else:
            set_admin_password(new_user or cfg["admin_username"], new_pass or None)
            msg = "Settings updated."
            cfg = get_config()

    body = render_template_string("""
      <h2>Settings</h2>
      <div class="card">
        <h3>Admin account</h3>
        <form method="post">
          <div class="form-row">
            <div>
              <label>Username</label>
              <input type="text" name="username" value="{{ cfg.admin_username }}">
            </div>
          </div>
          <div class="form-row">
            <div>
              <label>New password (optional)</label>
              <input type="password" name="password" placeholder="Leave blank to keep current">
            </div>
            <div>
              <label>Confirm password</label>
              <input type="password" name="confirm" placeholder="Repeat new password">
            </div>
          </div>
          {% if error %}
            <div class="error-msg">{{ error }}</div>
          {% endif %}
          {% if msg %}
            <div class="muted" style="color:#4ade80;margin-top:4px;">{{ msg }}</div>
          {% endif %}
          <button class="btn" type="submit" style="margin-top:8px;">Save settings</button>
        </form>
      </div>
    """, cfg=cfg, msg=msg, error=error)
    return render_template_string(
        LAYOUT_TEMPLATE,
        title=APP_TITLE,
        page_title="Settings",
        body=body,
        active="settings",
        stats=stats,
    )


# =============================
# Lancement
# =============================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Simple proxy management panel")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=1991)
    args = parser.parse_args()

    ensure_config()
    ensure_db()

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
