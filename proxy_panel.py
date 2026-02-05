#!/usr/bin/env python3
import os
import json
import datetime
from functools import wraps
from io import BytesIO

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

# -----------------------------
# Configuration de base
# -----------------------------

APP_TITLE = "Eagle Proxy Panel"

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
    proxies = {"http": proxy_url, "https": proxy_url}

    try:
        r = requests.get(CHECK_TEST_URL, proxies=proxies, timeout=timeout)
        return 200 <= r.status_code < 400
    except Exception:
        return False


# =============================
# Templates
# =============================

# ---- LOGIN : thème “enterprise” sombre / bleu ----
LOGIN_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ title }} - Login</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    * { box-sizing:border-box; }
    body {
      margin: 0;
      font-family: system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      /* fond data center bleu/violet */
      background-image:
        linear-gradient(135deg, rgba(15,23,42,0.92), rgba(30,64,175,0.92)),
        url("https://images.pexels.com/photos/4219643/pexels-photo-4219643.jpeg?auto=compress&cs=tinysrgb&w=1600");
      background-size: cover;
      background-position: center;
      background-attachment: fixed;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #0f172a;
    }
    .card {
      width: 380px;
      border-radius: 26px;
      padding: 26px 30px 26px;
      background:
        radial-gradient(circle at top left, rgba(129,140,248,0.22), transparent 60%),
        radial-gradient(circle at bottom right, rgba(56,189,248,0.20), transparent 60%),
        #ffffff;
      border: 1px solid rgba(129,140,248,0.7);
      box-shadow:
        0 18px 40px rgba(15,23,42,0.85),
        0 0 40px rgba(59,130,246,0.65);
    }

    /* --- LOGO EAGLE (image) --- */
    .eagle-logo-wrap {
      display:flex;
      flex-direction:column;
      align-items:center;
      justify-content:center;
      margin-bottom:18px;
    }
    .eagle-logo-img {
      width:80px;
      height:80px;
      border-radius:24px;
      object-fit:cover;
      box-shadow:
        0 0 20px rgba(251,191,36,0.8),
        0 0 35px rgba(30,64,175,0.8);
      border: 2px solid rgba(30,64,175,0.9);
      background:#020617;
    }
    .eagle-text-main {
      margin-top:10px;
      font-size:13px;
      letter-spacing:.32em;
      text-transform:uppercase;
      color:#6b7280;
    }
    .eagle-text-sub {
      margin-top:4px;
      font-size:14px;
      letter-spacing:.18em;
      text-transform:uppercase;
      background: linear-gradient(135deg,#3b82f6,#a855f7);
      -webkit-background-clip:text;
      background-clip:text;
      color:transparent;
    }

    h1 {
      margin: 0 0 6px;
      font-size: 22px;
      color:#0f172a;
    }
    .subtitle {
      font-size: 13px;
      color: #6b7280;
      margin-bottom: 18px;
    }
    label {
      display: block;
      font-size: 13px;
      color: #111827;
      margin-bottom: 5px;
    }
    input {
      width: 100%;
      border-radius: 999px;
      border: 1px solid rgba(148,163,184,0.9);
      background: #f9fafb;
      color: #0f172a;
      font-size: 14px;
      padding: 9px 12px;
      outline: none;
    }
    input:focus {
      border-color: #3b82f6;
      box-shadow: 0 0 0 1px rgba(59,130,246,0.55);
      background:#ffffff;
    }
    .field {
      margin-bottom: 14px;
    }
    .btn {
      margin-top: 6px;
      width: 100%;
      border-radius: 999px;
      border: none;
      padding: 10px 0;
      font-size: 14px;
      font-weight: 600;
      letter-spacing: .08em;
      text-transform: uppercase;
      cursor: pointer;
      color: #f9fafb;
      background: linear-gradient(135deg,#3b82f6,#a855f7);
      box-shadow:
        0 10px 24px rgba(59,130,246,0.7),
        0 0 30px rgba(129,140,248,0.9);
    }
    .btn:hover { filter: brightness(1.05); }
    .error {
      margin-top: 10px;
      font-size: 13px;
      color: #b91c1c;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="eagle-logo-wrap">
      <img src="/static/eagle_logo.png" alt="Eagle logo" class="eagle-logo-img">
      <div class="eagle-text-main">EAGLE</div>
      <div class="eagle-text-sub">Eagle Proxy Panel</div>
    </div>

    <h1>Sign in</h1>
    <div class="subtitle">Secure access to your proxy management dashboard.</div>

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
      --bg-main: #0f172a;
      --text-main: #0f172a;
      --text-muted: #6b7280;
      --accent-primary: #3b82f6;   /* bleu */
      --accent-secondary: #a855f7; /* mauve */
      --accent-ok: #16a34a;
      --accent-fail: #b91c1c;
      --accent-warn: #eab308;
    }
    * { box-sizing:border-box; }
    body {
      margin:0;
      font-family: system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      background-image:
        radial-gradient(circle at 0% 0%, rgba(59,130,246,0.25), transparent 60%),
        radial-gradient(circle at 100% 0%, rgba(168,85,247,0.20), transparent 60%),
        linear-gradient(180deg, #0f172a, #020617);
      min-height:100vh;
      color:var(--text-main);
    }
    .shell {
      max-width: 1280px;
      margin: 0 auto;
      padding: 18px 20px 30px;
    }
    header {
      display:flex;
      align-items:center;
      justify-content:space-between;
      margin-bottom:18px;
    }

    /* HEADER LOGO AVEC IMAGE */
    .brand {
      display:flex;
      align-items:center;
      gap:10px;
    }
    .eagle-header-logo {
      width:32px;
      height:32px;
      border-radius:10px;
      object-fit:cover;
      box-shadow:
        0 0 14px rgba(251,191,36,0.9),
        0 0 24px rgba(37,99,235,0.8);
      border:2px solid rgba(37,99,235,0.9);
      background:#020617;
    }
    .eagle-header-text {
      display:flex;
      flex-direction:column;
      gap:2px;
    }
    .eagle-header-title {
      font-size:13px;
      letter-spacing:.20em;
      text-transform:uppercase;
      background: linear-gradient(135deg,#3b82f6,#a855f7);
      -webkit-background-clip:text;
      background-clip:text;
      color:transparent;
    }
    .eagle-header-sub {
      font-size:11px;
      color:var(--text-muted);
    }

    nav {
      display:flex;
      gap:10px;
      align-items:center;
    }
    .nav-link {
      font-size:13px;
      padding:7px 14px;
      border-radius:999px;
      color:#0f172a;
      text-decoration:none;
      border:1px solid rgba(209,213,219,0.9);
      background:#f9fafb;
    }
    .nav-link:hover {
      border-color:var(--accent-primary);
      box-shadow:0 0 14px rgba(59,130,246,0.5);
    }
    .nav-link.active {
      color:#f9fafb;
      background:linear-gradient(135deg,var(--accent-primary),var(--accent-secondary));
      border-color:transparent;
      box-shadow:
        0 0 18px rgba(59,130,246,0.8),
        0 0 28px rgba(129,140,248,0.9);
    }
    .logout-link {
      font-size:12px;
      padding:7px 11px;
      border-radius:999px;
      border:1px solid rgba(209,213,219,0.9);
      background:#ffffff;
      color:var(--text-muted);
      text-decoration:none;
    }
    .logout-link:hover {
      border-color:var(--accent-secondary);
      color:#111827;
      box-shadow:0 0 14px rgba(168,85,247,0.4);
    }

    h2 {
      margin:0 0 14px;
      font-size:20px;
      color:#0f172a;
    }

    .grid {
      display:grid;
      grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
      gap:14px;
      margin-bottom:18px;
    }
    .card {
      background:#ffffff;
      border-radius:18px;
      padding:16px 18px;
      border:1px solid rgba(209,213,219,0.9);
      box-shadow:
        0 10px 30px rgba(15,23,42,0.25);
    }
    .card h3 {
      margin:0 0 6px;
      font-size:14px;
      color:#111827;
    }
    .card .big {
      font-size:28px;
      font-weight:600;
      color:#111827;
    }
    .muted {
      font-size:12px;
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
      border-bottom:1px solid rgba(229,231,235,0.9);
    }
    th {
      font-size:11px;
      text-transform:uppercase;
      letter-spacing:.08em;
      color:var(--text-muted);
    }
    tr:hover td {
      background:#f9fafb;
    }

    .pill {
      display:inline-block;
      padding:3px 8px;
      border-radius:999px;
      font-size:11px;
      border:1px solid rgba(209,213,219,0.9);
      color:#111827;
      background:#f3f4ff;
    }

    .btn {
      display:inline-flex;
      align-items:center;
      justify-content:center;
      padding:7px 15px;
      border-radius:999px;
      border:none;
      cursor:pointer;
      font-size:13px;
      color:#f9fafb;
      background:linear-gradient(135deg,var(--accent-primary),var(--accent-secondary));
      box-shadow:
        0 10px 25px rgba(59,130,246,0.6),
        0 0 24px rgba(129,140,248,0.8);
    }
    .btn:hover { filter:brightness(1.05); }

    .btn-secondary {
      background:#f9fafb;
      border:1px solid rgba(209,213,219,0.9);
      color:#111827;
      box-shadow:none;
    }
    .btn-secondary:hover {
      border-color:var(--accent-primary);
      box-shadow:0 0 12px rgba(59,130,246,0.4);
    }

    .status-badge {
      padding:3px 9px;
      border-radius:999px;
      font-size:11px;
      font-weight:500;
      border:1px solid transparent;
    }
    .status-ok {
      background:rgba(22,163,74,0.10);
      color:#166534;
      border-color:rgba(22,163,74,0.9);
    }
    .status-fail {
      background:rgba(248,113,113,0.10);
      color:#b91c1c;
      border-color:rgba(239,68,68,0.9);
    }
    .status-unknown {
      background:rgba(209,213,219,0.4);
      color:#374151;
      border-color:rgba(156,163,175,0.9);
    }
    .status-checking {
      background:rgba(251,191,36,0.10);
      color:#92400e;
      border-color:rgba(217,119,6,0.9);
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
      border-radius:999px;
      border:1px solid rgba(209,213,219,0.9);
      background:#f9fafb;
      color:#111827;
      padding:7px 12px;
      min-width:140px;
      font-size:13px;
    }
    .form-row input:focus {
      outline:none;
      border-color:var(--accent-primary);
      box-shadow:0 0 0 1px rgba(59,130,246,0.4);
      background:#ffffff;
    }

    .error-msg {
      color:#b91c1c;
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
    .progress-bar-outer {
      width:100%;
      height:7px;
      border-radius:999px;
      background:#e5e7eb;
      overflow:hidden;
      margin-top:6px;
      border:1px solid rgba(209,213,219,0.9);
    }
    .progress-bar-inner {
      height:100%;
      width:0%;
      border-radius:999px;
      background:linear-gradient(90deg,#3b82f6,#a855f7);
      transition:width .18s ease-out;
    }

    @media (max-width: 680px) {
      header { flex-direction:column; align-items:flex-start; gap:10px; }
      .shell { padding:14px 14px 24px; }
    }

    .pre-proxies {
      width:100%;
      min-height:260px;
      border-radius:16px;
      border:1px solid rgba(209,213,219,0.9);
      background:#f9fafb;
      padding:10px;
      color:#111827;
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
        <img src="/static/eagle_logo.png" alt="Eagle logo" class="eagle-header-logo">
        <div class="eagle-header-text">
          <div class="eagle-header-title">Eagle Proxy Panel</div>
          <div class="eagle-header-sub">Internal proxy management console</div>
        </div>
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
          <div class="muted">Each client download has its own text file.</div>
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
            Assigned: <span style="color:#fed7aa;">{{ stats.assigned_proxies }}</span><br>
            Available: <span style="color:#bbf7d0;">{{ stats.available_proxies }}</span>
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
            Available: <span style="color:#bbf7d0;">{{ stats.available_proxies }}</span> ·
            Assigned: <span style="color:#fed7aa;">{{ stats.assigned_proxies }}</span>
          </div>
          <button class="btn" id="check-all-btn">Check ALL Proxies</button>
        </div>
        <div class="muted" style="margin-top:4px;font-size:11px;">
          Performs an HTTPS request to Google using each proxy (timeout {{ timeout }}s).
        </div>

        <div id="progress-wrapper" class="progress-wrapper" style="display:none;">
          <div>
            <span id="progress-label">0%</span>
            &nbsp;·&nbsp;
            <span id="progress-detail">Waiting…</span>
          </div>
          <div class="progress-bar-outer">
            <div class="progress-bar-inner" id="progress-bar"></div>
          </div>
        </div>

        <div id="check-summary" class="muted" style="margin-top:8px;font-size:12px;display:none;">
          Last check:
          <span id="sum-ok" style="color:#bbf7d0;">0 OK</span>
          &nbsp;·&nbsp;
          <span id="sum-fail" style="color:#fed7aa;">0 Failed</span>
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
        const progressWrapper = document.getElementById('progress-wrapper');
        const progressBar = document.getElementById('progress-bar');
        const progressLabel = document.getElementById('progress-label');
        const progressDetail = document.getElementById('progress-detail');

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

        async function runCheckAll() {
          const rows = Array.from(table.querySelectorAll('tbody tr'));
          const total = rows.length;
          if (!total) return;

          let okCount = 0;
          let failCount = 0;

          progressWrapper.style.display = 'block';
          summaryDiv.style.display = 'none';
          progressBar.style.width = '0%';
          progressLabel.textContent = '0%';
          progressDetail.textContent = 'Starting...';

          // Met tous en CHECKING au début
          rows.forEach(row => {
            const proxy = row.getAttribute('data-proxy');
            setStatus(proxy, 'checking');
          });

          for (let i = 0; i < total; i++) {
            const row = rows[i];
            const proxy = row.getAttribute('data-proxy');
            progressDetail.textContent = 'Checking ' + (i + 1) + ' / ' + total;

            try {
              const resp = await fetch('{{ url_for("proxies_check_one") }}', {
                method: 'POST',
                headers: {
                  'Content-Type': 'application/json',
                  'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({ proxy: proxy })
              });
              const data = await resp.json();
              const status = (data && data.status === 'ok') ? 'ok' : 'fail';
              if (status === 'ok') okCount++; else failCount++;
              setStatus(proxy, status);
            } catch (e) {
              failCount++;
              setStatus(proxy, 'fail');
            }

            const pct = Math.round(((i + 1) / total) * 100);
            progressBar.style.width = pct + '%';
            progressLabel.textContent = pct + '%';
          }

          progressDetail.textContent = 'Completed';
          summaryDiv.style.display = 'block';
          sumOkSpan.textContent = okCount + ' OK';
          sumFailSpan.textContent = failCount + ' Failed';
        }

        if (checkBtn) {
          checkBtn.addEventListener('click', () => {
            runCheckAll();
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


@app.route("/proxies/check-one", methods=["POST"])
@login_required
def proxies_check_one():
    """
    Vérifie un seul proxy (appelé en boucle par le JS).
    """
    data = request.get_json(silent=True) or {}
    proxy_line = (data.get("proxy") or "").strip()
    if not proxy_line:
        return jsonify({"status": "fail", "error": "no proxy"}), 400

    ok = check_proxy(proxy_line)
    return jsonify({"status": "ok" if ok else "fail"})


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
            <div class="muted" style="color:#bbf7d0;margin-top:4px;">{{ msg }}</div>
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
