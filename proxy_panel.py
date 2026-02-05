#!/usr/bin/env python3
import os
import json
import datetime
import socket
from functools import wraps

from flask import Flask, request, redirect, url_for, render_template_string, session, send_file, abort
from werkzeug.security import generate_password_hash, check_password_hash

APP_TITLE = "Proxy Panel"

DEFAULT_CONFIG = {
    "admin_username": "admin",
    "password_hash": generate_password_hash("admin"),
    "listen_host": "0.0.0.0",
    "listen_port": 1991,
    "proxy_source": "/root/proxies.txt",
    "db_file": "/root/proxy_panel_db.json",
    "config_file": "/root/proxy_panel_config.json",
}

# =============================
# Helper functions for config/db
# =============================

def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def get_config():
    cfg_path = DEFAULT_CONFIG["config_file"]
    cfg = load_json(cfg_path, DEFAULT_CONFIG)
    # ensure all keys exist
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg):
    cfg_path = DEFAULT_CONFIG["config_file"]
    save_json(cfg_path, cfg)


def get_db():
    cfg = get_config()
    db_path = cfg["db_file"]
    db = load_json(db_path, {"clients": {}, "assignments": {}})
    db.setdefault("clients", {})
    db.setdefault("assignments", {})
    return db


def save_db(db):
    cfg = get_config()
    db_path = cfg["db_file"]
    save_json(db_path, db)


# =============================
# Proxy parsing / assignment
# =============================

def parse_proxy_line(line):
    """
    Parse lines in format:
      host:port
      host:port:user:pass
    Return dict with keys: host, port, user, password (user/password optional).
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split(":")
    if len(parts) < 2:
        return None
    host = parts[0]
    port = parts[1]
    user = parts[2] if len(parts) > 2 else None
    password = parts[3] if len(parts) > 3 else None
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
    }


def load_all_proxies():
    cfg = get_config()
    path = cfg["proxy_source"]
    proxies = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                p = parse_proxy_line(line)
                if p:
                    proxies.append(p)
    return proxies


def build_proxy_url(proxy):
    """Return proxy URL string from parsed proxy dict, e.g. http://user:pass@host:port."""
    auth = ""
    if proxy.get("user") and proxy.get("password"):
        auth = f"{proxy['user']}:{proxy['password']}@"
    return f"http://{auth}{proxy['host']}:{proxy['port']}"


def get_all_proxy_ids():
    """Use host:port as a unique proxy key."""
    proxies = load_all_proxies()
    proxy_ids = []
    for p in proxies:
        proxy_ids.append(f"{p['host']}:{p['port']}")
    return proxy_ids


def compute_stats():
    cfg = get_config()
    db = get_db()
    proxies = load_all_proxies()
    total_proxies = len(proxies)
    clients_count = len(db["clients"])

    all_ids = set(get_all_proxy_ids())
    assignments = db["assignments"]
    assigned_ids = set(assignments.values())
    available_ids = all_ids - assigned_ids

    return {
        "total_proxies": total_proxies,
        "clients_count": clients_count,
        "assigned_proxies": len(assigned_ids),
        "available_proxies": len(available_ids),
        "proxy_source": cfg["proxy_source"],
        "db_file": cfg["db_file"],
    }


def assign_proxies_evenly():
    """
    Read clients and proxies. Assign proxies to each client as evenly as possible.
    Clients have "count" specifying desired number of proxies.
    """
    db = get_db()
    clients = db["clients"]
    proxies = get_all_proxy_ids()
    assignments = {}  # proxy_id -> client_name

    if not clients or not proxies:
        db["assignments"] = assignments
        save_db(db)
        return

    client_list = [(name, info["count"]) for name, info in clients.items()]
    client_list.sort(key=lambda x: x[0])

    expanded_clients = []
    for name, count in client_list:
        for _ in range(count):
            expanded_clients.append(name)

    if not expanded_clients:
        db["assignments"] = {}
        save_db(db)
        return

    assigned = {}
    i = 0
    for p in proxies:
        client_name = expanded_clients[i % len(expanded_clients)]
        assigned[p] = client_name
        i += 1

    db["assignments"] = assigned
    save_db(db)


def get_client_proxies(client_name):
    """Return list of (proxy_id, proxy_url) for given client."""
    db = get_db()
    assignments = db["assignments"]
    proxies = load_all_proxies()
    proxy_map = {}
    for p in proxies:
        pid = f"{p['host']}:{p['port']}"
        proxy_map[pid] = p

    result = []
    for pid, assigned_client in assignments.items():
        if assigned_client == client_name and pid in proxy_map:
            p = proxy_map[pid]
            result.append((pid, build_proxy_url(p)))
    return result


def check_proxy(proxy, timeout=2.0):
    """
    Basic TCP connectivity check.
    """
    try:
        host = proxy["host"]
        port = int(proxy["port"])
    except Exception:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((host, port))
        return True
    except Exception:
        return False


# =============================
# Flask app and auth
# =============================

app = Flask(__name__)
app.secret_key = os.environ.get("PROXY_PANEL_SECRET", "change_me_please")


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


# =============================
# TEMPLATES (HTML/CSS)
# =============================

# ---- LOGIN THEME (modern + bg server) ----
LOGIN_TEMPLATE = """
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>{{ title }} - Connexion</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    * { box-sizing:border-box; }

    :root {
      --bg-overlay: rgba(2,6,23,0.90);
      --card-bg: rgba(15,23,42,0.92);
      --card-border: rgba(148,163,184,0.35);
      --text-main: #e5e7eb;
      --text-muted: #9ca3af;
      --accent: #22c55e;
      --accent-alt: #2563eb;
      --danger: #f97316;
    }

    body {
      margin:0;
      min-height:100vh;
      font-family: system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      color: var(--text-main);
      background-image:
        linear-gradient(120deg, rgba(15,23,42,0.92), rgba(15,23,42,0.96)),
        url("https://images.pexels.com/photos/325229/pexels-photo-325229.jpeg?auto=compress&cs=tinysrgb&w=1600");
      background-size: cover;
      background-position: center;
      background-attachment: fixed;
    }

    .page {
      min-height:100vh;
      display:flex;
      align-items:center;
      justify-content:center;
      padding:24px;
      background: radial-gradient(circle at top left, rgba(37,99,235,0.30), transparent 55%);
    }

    .shell {
      width:100%;
      max-width:980px;
      display:grid;
      grid-template-columns: minmax(0,1.2fr) minmax(0,1fr);
      gap:28px;
    }

    .hero-card,
    .form-card {
      position:relative;
      border-radius:22px;
      padding:22px 24px;
      border:1px solid var(--card-border);
      background:
        linear-gradient(135deg, rgba(15,23,42,0.98), rgba(15,23,42,0.96));
      box-shadow:
        0 18px 40px rgba(0,0,0,0.75),
        0 0 0 1px rgba(15,23,42,0.9);
      backdrop-filter: blur(4px);
    }

    .hero-card::before {
      content:"";
      position:absolute;
      inset:-1px;
      border-radius:inherit;
      background:
        radial-gradient(circle at top left, rgba(59,130,246,0.40), transparent 60%),
        radial-gradient(circle at top right, rgba(236,72,153,0.40), transparent 60%);
      mix-blend-mode: screen;
      opacity:0.6;
      pointer-events:none;
      z-index:-1;
    }

    .badge {
      display:inline-flex;
      align-items:center;
      gap:6px;
      padding:6px 12px;
      border-radius:999px;
      font-size:12px;
      color:#e5e7eb;
      background: radial-gradient(circle at top left,#4f46e5,#22c55e);
      box-shadow:0 12px 30px rgba(59,130,246,0.75);
    }
    .badge-dot {
      width:8px;
      height:8px;
      border-radius:999px;
      background:#22c55e;
      box-shadow:0 0 0 4px rgba(34,197,94,0.35);
    }

    .hero-title {
      margin:10px 0 6px;
      font-size:26px;
      font-weight:650;
    }
    .hero-subtitle {
      font-size:14px;
      color:var(--text-muted);
      max-width:360px;
    }

    .hero-tags {
      display:flex;
      flex-wrap:wrap;
      gap:8px;
      margin-top:18px;
    }
    .hero-tag {
      font-size:11px;
      padding:6px 10px;
      border-radius:999px;
      border:1px solid rgba(148,163,184,0.5);
      background:rgba(15,23,42,0.92);
    }
    .hero-metrics {
      display:flex;
      gap:16px;
      margin-top:22px;
      font-size:12px;
    }
    .hero-metric-label { color:var(--text-muted); }
    .hero-metric-value { font-size:18px; font-weight:600; }

    .form-card h2 {
      margin:0 0 6px;
      font-size:18px;
    }
    .form-subtitle {
      font-size:13px;
      color:var(--text-muted);
      margin-bottom:16px;
    }
    .field {
      margin-bottom:14px;
    }
    label {
      display:block;
      font-size:12px;
      margin-bottom:6px;
      color:var(--text-muted);
    }
    input {
      width:100%;
      border-radius:999px;
      border:1px solid rgba(148,163,184,0.4);
      background:rgba(15,23,42,0.98);
      color:var(--text-main);
      padding:9px 14px;
      font-size:14px;
      outline:none;
    }
    input::placeholder {
      color:#6b7280;
    }
    input:focus {
      border-color:var(--accent-alt);
      box-shadow:0 0 0 1px rgba(37,99,235,0.55);
    }

    .btn {
      margin-top:6px;
      width:100%;
      border:none;
      border-radius:999px;
      padding:10px 0;
      font-size:14px;
      font-weight:600;
      letter-spacing:.03em;
      text-transform:uppercase;
      cursor:pointer;
      color:#f9fafb;
      background:linear-gradient(135deg,#16a34a,#22c55e);
      box-shadow:0 14px 40px rgba(22,163,74,0.9);
      transition:transform .08s ease, box-shadow .08s ease, filter .08s ease;
    }
    .btn:hover {
      filter:brightness(1.05);
      transform:translateY(-1px);
      box-shadow:0 18px 55px rgba(22,163,74,1);
    }

    .error {
      margin-top:10px;
      font-size:13px;
      color:var(--danger);
    }

    .footer {
      margin-top:18px;
      font-size:11px;
      color:var(--text-muted);
      text-align:center;
    }

    @media (max-width: 900px) {
      .shell {
        grid-template-columns: minmax(0,1fr);
      }
      .hero-card {
        display:none;
      }
    }
  </style>
</head>
<body>
  <div class="page">
    <div class="shell">
      <section class="hero-card">
        <div class="badge">
          <span class="badge-dot"></span>
          Proxy Panel
        </div>
        <h1 class="hero-title">Dashboard Proxy Panel</h1>
        <p class="hero-subtitle">
          Interface moderne, rapide et lisible. Connexion sécurisée,
          gestion centralisée et monitoring de tes proxys.
        </p>
        <div class="hero-tags">
          <div class="hero-tag">Sessions</div>
          <div class="hero-tag">Monitoring</div>
          <div class="hero-tag">Exports</div>
        </div>
        <div class="hero-metrics">
          <div>
            <div class="hero-metric-label">Source</div>
            <div class="hero-metric-value">Fichier local</div>
          </div>
          <div>
            <div class="hero-metric-label">Sécurité</div>
            <div class="hero-metric-value">Authentifiée</div>
          </div>
        </div>
      </section>

      <section class="form-card">
        <h2>Connexion</h2>
        <p class="form-subtitle">
          Entre tes identifiants pour accéder au panel.
        </p>
        <form method="post">
          <div class="field">
            <label for="username">Nom d'utilisateur</label>
            <input id="username" name="username" autocomplete="username" value="{{ default_user }}">
          </div>
          <div class="field">
            <label for="password">Mot de passe</label>
            <input id="password" name="password" type="password" autocomplete="current-password">
          </div>
          {% if error %}
          <div class="error">{{ error }}</div>
          {% endif %}
          <button class="btn" type="submit">Se connecter</button>
        </form>
        <div class="footer">
          © {{ title }} · Secure Panel
        </div>
      </section>
    </div>
  </div>
</body>
</html>
"""

# ---- LAYOUT : thème dashboard corporate ----
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
      --bg-elevated: #020617;
      --text-main: #e5e7eb;
      --text-muted: #9ca3af;
      --accent-primary: #2563eb;
      --accent-primary-soft: rgba(37,99,235,0.18);
      --accent-secondary: #38bdf8;
      --accent-ok: #22c55e;
      --accent-fail: #f97316;
      --accent-warn: #eab308;
    }
    * { box-sizing:border-box; }
    body {
      margin:0;
      font-family: system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      background-image:
        linear-gradient(140deg, rgba(15,23,42,0.94), rgba(15,23,42,0.96)),
        url("https://images.pexels.com/photos/325229/pexels-photo-325229.jpeg?auto=compress&cs=tinysrgb&w=1600");
      background-size: cover;
      background-position: center;
      background-attachment: fixed;
      color:var(--text-main);
      min-height:100vh;
    }
    .shell {
      max-width:1120px;
      margin:20px auto 32px;
      padding:18px 20px 30px;
      border-radius:22px;
      background:
        radial-gradient(circle at top left, rgba(37,99,235,0.18), transparent 58%),
        radial-gradient(circle at bottom right, rgba(56,189,248,0.12), transparent 60%),
        linear-gradient(130deg, rgba(15,23,42,0.96), rgba(15,23,42,0.98));
      border:1px solid rgba(148,163,184,0.4);
      box-shadow:
        0 18px 60px rgba(0,0,0,0.9),
        0 0 0 1px rgba(15,23,42,0.9);
      backdrop-filter: blur(10px);
    }
    header {
      display:flex;
      align-items:center;
      justify-content:space-between;
      margin-bottom:18px;
    }
    .brand {
      display:flex;
      flex-direction:column;
      gap:2px;
    }
    .brand-title {
      font-weight:700;
      letter-spacing:.2em;
      font-size:12px;
      text-transform:uppercase;
    }
    .brand-title span {
      color:#60a5fa;
    }
    .brand-sub {
      font-size:11px;
      color:var(--text-muted);
    }
    nav {
      display:flex;
      gap:10px;
      align-items:center;
      font-size:13px;
    }
    .nav-link {
      position:relative;
      padding:7px 14px;
      border-radius:999px;
      text-decoration:none;
      color:#e5e7eb;
      border:1px solid transparent;
      background:rgba(15,23,42,0.6);
    }
    .nav-link::before {
      content:"";
      position:absolute;
      inset:0;
      border-radius:999px;
      background:radial-gradient(circle at top left, rgba(59,130,246,0.3), transparent 60%);
      opacity:0;
      transition:opacity .15s ease;
      z-index:-1;
    }
    .nav-link:hover::before {
      opacity:1;
    }
    .nav-link.active {
      border-color:rgba(59,130,246,0.9);
      background:radial-gradient(circle at top left, rgba(59,130,246,0.45), transparent 62%);
      box-shadow:0 12px 30px rgba(59,130,246,0.6);
    }
    .logout-link {
      margin-left:6px;
      padding:6px 12px;
      border-radius:999px;
      font-size:12px;
      border:1px solid rgba(148,163,184,0.4);
      color:var(--text-muted);
      text-decoration:none;
      background:rgba(15,23,42,0.8);
    }
    .logout-link:hover {
      color:#f9fafb;
      border-color:rgba(248,250,252,0.4);
    }

    h2 {
      margin:0 0 14px;
      font-size:20px;
    }

    .grid {
      display:grid;
      grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
      gap:14px;
      margin-bottom:18px;
    }
    .card {
      background:
        radial-gradient(circle at top left, rgba(37,99,235,0.10), transparent 55%),
        linear-gradient(to bottom right, #020617, #020617);
      border-radius:16px;
      padding:16px 18px;
      border:1px solid rgba(148,163,184,0.32);
      box-shadow:0 20px 60px rgba(15,23,42,1);
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
      color:var(--text-muted);
      font-size:12px;
    }
    .pill {
      display:inline-flex;
      align-items:center;
      gap:6px;
      padding:4px 9px;
      border-radius:999px;
      border:1px solid rgba(148,163,184,0.4);
      font-size:11px;
    }
    .pill-dot {
      width:8px;
      height:8px;
      border-radius:999px;
    }

    table {
      width:100%;
      border-collapse:collapse;
      font-size:13px;
    }
    th, td {
      padding:8px 10px;
      text-align:left;
      border-bottom:1px solid rgba(30,41,59,0.9);
    }
    th {
      font-size:12px;
      color:var(--text-muted);
      text-transform:uppercase;
      letter-spacing:.05em;
      background:rgba(15,23,42,0.95);
      position:sticky;
      top:0;
      z-index:1;
    }
    tr:nth-child(even) td {
      background:rgba(15,23,42,0.85);
    }
    tr:hover td {
      background:rgba(15,23,42,1);
    }

    .badge {
      display:inline-flex;
      align-items:center;
      gap:6px;
      padding:4px 9px;
      border-radius:999px;
      font-size:11px;
      background:rgba(15,23,42,0.95);
      border:1px solid rgba(148,163,184,0.4);
    }
    .status-dot {
      width:8px;
      height:8px;
      border-radius:999px;
    }
    .status-ok {
      background:#22c55e;
      box-shadow:0 0 0 4px rgba(34,197,94,0.4);
    }
    .status-fail {
      background:#f97316;
      box-shadow:0 0 0 4px rgba(248,113,113,0.35);
    }

    .btn {
      display:inline-flex;
      align-items:center;
      justify-content:center;
      gap:6px;
      font-size:13px;
      border-radius:999px;
      border:1px solid transparent;
      padding:6px 12px;
      cursor:pointer;
      text-decoration:none;
      background:linear-gradient(135deg,#2563eb,#22c55e);
      color:#f9fafb;
      box-shadow:0 14px 38px rgba(37,99,235,0.65);
    }
    .btn-secondary {
      background:rgba(15,23,42,0.95);
      border-color:rgba(148,163,184,0.4);
      box-shadow:none;
      color:var(--text-main);
    }
    .btn-danger {
      background:linear-gradient(135deg,#b91c1c,#f97316);
      box-shadow:0 14px 38px rgba(185,28,28,0.75);
    }

    .btn-small {
      padding:4px 9px;
      font-size:12px;
    }

    .table-actions {
      display:flex;
      gap:6px;
    }

    .form-row {
      display:flex;
      flex-wrap:wrap;
      gap:10px;
      margin-bottom:12px;
      align-items:flex-end;
    }
    .form-row label {
      display:block;
      font-size:12px;
      color:var(--text-muted);
      margin-bottom:4px;
    }
    .form-row input,
    .form-row select {
      border-radius:999px;
      border:1px solid rgba(148,163,184,0.4);
      background:rgba(15,23,42,0.98);
      color:#e5e7eb;
      padding:7px 10px;
      min-width:120px;
      font-size:13px;
    }
    .form-row input:focus,
    .form-row select:focus {
      outline:none;
      border-color:var(--accent-primary);
      box-shadow:0 0 0 1px rgba(37,99,235,0.5);
    }

    .error-msg {
      color:var(--accent-fail);
      font-size:12px;
      margin-top:4px;
    }

    .footer-note {
      margin-top:18px;
      font-size:11px;
      color:var(--text-muted);
      text-align:center;
    }

    .progress-wrapper {
      margin-top:10px;
      font-size:12px;
      color:var(--text-muted);
    }
    .progress-bar-bg {
      margin-top:6px;
      width:100%;
      height:6px;
      border-radius:999px;
      background:rgba(15,23,42,0.9);
      overflow:hidden;
      border:1px solid rgba(30,64,175,0.8);
    }
    .progress-bar-fill {
      height:100%;
      border-radius:999px;
      background:linear-gradient(90deg,#2563eb,#22c55e);
    }

    @media (max-width: 768px) {
      header {
        flex-direction:column;
        align-items:flex-start;
        gap:10px;
      }
      nav {
        flex-wrap:wrap;
        justify-content:flex-start;
      }
      .shell {
        margin:12px;
        padding:14px 12px 22px;
      }
      th, td {
        padding:6px 6px;
      }
    }

    .pre-proxies {
      width:100%;
      min-height:260px;
      border-radius:16px;
      border:1px solid rgba(30,64,175,0.8);
      background:rgba(15,23,42,0.96);
      padding:10px;
      color:#e5e7eb;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      font-size:12px;
      resize:vertical;
    }

    .clipboard-info {
      margin-top:6px;
      font-size:11px;
      color:var(--text-muted);
    }

  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div class="brand">
        <div class="brand-title"><span>{{ title }}</span></div>
        <div class="brand-sub">Internal proxy management console</div>
      </div>
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
      Local-only admin panel · Proxy source: {{ stats.proxy_source }} · DB: {{ stats.db_file }}
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
            <span style="color:#bbf7d0;">Available: {{ stats.available_proxies }}</span>
            &nbsp;·&nbsp;
            <span style="color:#fed7aa;">Assigned: {{ stats.assigned_proxies }}</span>
          </div>
        </div>
        <div class="card">
          <h3>Clients</h3>
          <div class="big">{{ stats.clients_count }}</div>
          <div class="muted">Configured clients</div>
        </div>
      </div>

      <div class="progress-wrapper">
        <div>Usage of proxy pool</div>
        {% set total = stats.total_proxies if stats.total_proxies > 0 else 1 %}
        {% set used = stats.assigned_proxies %}
        {% set pct = (used * 100) // total %}
        <div class="progress-bar-bg">
          <div class="progress-bar-fill" style="width: {{ pct }}%;"></div>
        </div>
        <div class="muted" style="margin-top:4px;">
          {{ used }} / {{ total }} proxies assigned ({{ pct }}%)
        </div>
      </div>
    """, stats=stats)
    return render_template_string(
        LAYOUT_TEMPLATE,
        title=APP_TITLE,
        page_title="Dashboard",
        active="dashboard",
        stats=stats,
        body=body,
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
            count = -1
        if not name:
            error = "Client name cannot be empty."
        elif count <= 0:
            error = "Proxy count must be a positive integer."
        else:
            db["clients"][name] = {"count": count}
            save_db(db)
            assign_proxies_evenly()
            return redirect(url_for("clients"))

    body = render_template_string("""
      <h2>Clients</h2>
      {% if error %}
        <div class="error-msg">{{ error }}</div>
      {% endif %}
      <form method="post" style="margin-bottom:14px;">
        <div class="form-row">
          <div>
            <label for="name">Client name</label>
            <input id="name" name="name" placeholder="Client name">
          </div>
          <div>
            <label for="count">Proxy count</label>
            <input id="count" name="count" type="number" min="1" placeholder="Example: 10">
          </div>
          <div>
            <button class="btn" type="submit">Add client</button>
          </div>
        </div>
      </form>

      <div class="card">
        <h3>Existing clients</h3>
        {% if not db.clients %}
          <div class="muted">No clients configured yet.</div>
        {% else %}
          <table>
            <thead>
              <tr>
                <th>Client</th>
                <th>Proxy count</th>
                <th>Assigned proxies</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
            {% for name, info in db.clients.items() %}
              {% set assigned = db.assignments.values()|list %}
              {% set assigned_count = assigned|select("equalto", name)|list|length %}
              <tr>
                <td>{{ name }}</td>
                <td>{{ info.count }}</td>
                <td>
                  <span class="badge">
                    <span class="status-dot status-ok"></span>
                    {{ assigned_count }}
                  </span>
                </td>
                <td>
                  <div class="table-actions">
                    <a class="btn btn-small btn-secondary" href="{{ url_for('client_proxies_view', client_name=name) }}">View proxies</a>
                    <a class="btn btn-small btn-secondary" href="{{ url_for('client_proxies_download', client_name=name) }}">Download</a>
                    <a class="btn btn-small btn-danger" href="{{ url_for('delete_client', client_name=name) }}" onclick="return confirm('Delete this client?');">Delete</a>
                  </div>
                </td>
              </tr>
            {% endfor %}
            </tbody>
          </table>
        {% endif %}
      </div>
    """, db=db, error=error)
    return render_template_string(
        LAYOUT_TEMPLATE,
        title=APP_TITLE,
        page_title="Clients",
        active="clients",
        stats=stats,
        body=body,
    )


@app.route("/clients/<client_name>/proxies")
@login_required
def client_proxies_view(client_name):
    stats = compute_stats()
    db = get_db()
    if client_name not in db["clients"]:
        abort(404)
    proxies = get_client_proxies(client_name)
    lines = [p[1] for p in proxies]
    text_block = "\n".join(lines)
    body = render_template_string("""
      <h2>Proxies for client: {{ client_name }}</h2>
      <div class="card">
        <h3>Proxy list</h3>
        <textarea class="pre-proxies" readonly id="proxyText">{{ text_block }}</textarea>
        <div class="clipboard-info">
          <button class="btn btn-small" type="button" onclick="copyProxies()">Copy to clipboard</button>
        </div>
      </div>
      <script>
        function copyProxies() {
          var el = document.getElementById("proxyText");
          el.select();
          document.execCommand("copy");
          alert("Proxies copied to clipboard.");
        }
      </script>
    """, client_name=client_name, text_block=text_block)
    return render_template_string(
        LAYOUT_TEMPLATE,
        title=APP_TITLE,
        page_title=f"Client {client_name}",
        active="clients",
        stats=stats,
        body=body,
    )


@app.route("/clients/<client_name>/download")
@login_required
def client_proxies_download(client_name):
    db = get_db()
    if client_name not in db["clients"]:
        abort(404)
    proxies = get_client_proxies(client_name)
    lines = [p[1] for p in proxies]
    text_block = "\n".join(lines)

    tmp_path = f"/tmp/proxies_{client_name}.txt"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(text_block)
    return send_file(tmp_path, as_attachment=True, download_name=f"{client_name}_proxies.txt")


@app.route("/clients/<client_name>/delete")
@login_required
def delete_client(client_name):
    db = get_db()
    if client_name in db["clients"]:
        del db["clients"][client_name]
        db["assignments"] = {pid: c for pid, c in db["assignments"].items() if c != client_name}
        save_db(db)
        assign_proxies_evenly()
    return redirect(url_for("clients"))


@app.route("/proxies")
@login_required
def proxies():
    stats = compute_stats()
    db = get_db()
    proxies = load_all_proxies()
    assignments = db["assignments"]
    rows = []
    for p in proxies:
        pid = f"{p['host']}:{p['port']}"
        client_name = assignments.get(pid)
        rows.append((pid, build_proxy_url(p), client_name))

    body = render_template_string("""
      <h2>Proxy pool</h2>
      <div class="card">
        {% if not rows %}
          <div class="muted">No proxies loaded. Configure "proxy_source" in settings.</div>
        {% else %}
          <table>
            <thead>
              <tr>
                <th>Proxy ID</th>
                <th>URL</th>
                <th>Assigned client</th>
              </tr>
            </thead>
            <tbody>
              {% for pid, url, client_name in rows %}
              <tr>
                <td>{{ pid }}</td>
                <td style="font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; font-size:12px;">
                  {{ url }}
                </td>
                <td>
                  {% if client_name %}
                    <span class="badge">
                      <span class="status-dot status-ok"></span>
                      {{ client_name }}
                    </span>
                  {% else %}
                    <span class="badge">
                      <span class="status-dot status-fail"></span>
                      Unassigned
                    </span>
                  {% endif %}
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        {% endif %}
      </div>
    """, rows=rows)
    return render_template_string(
        LAYOUT_TEMPLATE,
        title=APP_TITLE,
        page_title="Proxies",
        active="proxies",
        stats=stats,
        body=body,
    )


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    cfg = get_config()
    stats = compute_stats()
    error = None

    if request.method == "POST":
        listen_host = request.form.get("listen_host", "").strip()
        listen_port_str = request.form.get("listen_port", "").strip()
        proxy_source = request.form.get("proxy_source", "").strip()
        db_file = request.form.get("db_file", "").strip()
        new_user = request.form.get("admin_username", "").strip()
        new_pass = request.form.get("admin_password", "")

        try:
            listen_port = int(listen_port_str)
        except ValueError:
            listen_port = None

        if not listen_host or listen_port is None:
            error = "Invalid host/port."
        else:
            cfg["listen_host"] = listen_host
            cfg["listen_port"] = listen_port
            if proxy_source:
                cfg["proxy_source"] = proxy_source
            if db_file:
                cfg["db_file"] = db_file
            if new_user:
                cfg["admin_username"] = new_user
            if new_pass:
                cfg["password_hash"] = generate_password_hash(new_pass)
            save_config(cfg)
            return redirect(url_for("settings"))

    body = render_template_string("""
      <h2>Settings</h2>
      {% if error %}
        <div class="error-msg">{{ error }}</div>
      {% endif %}
      <div class="card">
        <form method="post">
          <div class="form-row">
            <div>
              <label for="listen_host">Listen host</label>
              <input id="listen_host" name="listen_host" value="{{ cfg.listen_host }}">
            </div>
            <div>
              <label for="listen_port">Listen port</label>
              <input id="listen_port" name="listen_port" type="number" value="{{ cfg.listen_port }}">
            </div>
          </div>
          <div class="form-row">
            <div style="flex:1;">
              <label for="proxy_source">Proxy source file</label>
              <input id="proxy_source" name="proxy_source" value="{{ cfg.proxy_source }}">
            </div>
          </div>
          <div class="form-row">
            <div style="flex:1;">
              <label for="db_file">DB file</label>
              <input id="db_file" name="db_file" value="{{ cfg.db_file }}">
            </div>
          </div>
          <div class="form-row">
            <div>
              <label for="admin_username">Admin username</label>
              <input id="admin_username" name="admin_username" value="{{ cfg.admin_username }}">
            </div>
            <div>
              <label for="admin_password">New admin password (optional)</label>
              <input id="admin_password" name="admin_password" type="password" placeholder="Leave blank to keep current">
            </div>
          </div>
          <button class="btn" type="submit" style="margin-top:8px;">Save settings</button>
        </form>
      </div>
    """, cfg=cfg, error=error)
    return render_template_string(
        LAYOUT_TEMPLATE,
        title=APP_TITLE,
        page_title="Settings",
        active="settings",
        stats=stats,
        body=body,
    )


def main():
    cfg = get_config()
    host = cfg.get("listen_host", "0.0.0.0")
    port = cfg.get("listen_port", 1991)
    try:
        port = int(port)
    except Exception:
        port = 1991
    print(f"[{datetime.datetime.now().isoformat()}] Starting {APP_TITLE} on {host}:{port}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
