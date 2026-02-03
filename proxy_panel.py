#!/usr/bin/env python3
import os
import json
import datetime
import argparse
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
    abort,
    jsonify,
)
from werkzeug.security import generate_password_hash, check_password_hash

APP_TITLE = "Proxy Panel"

PROXY_SOURCE_FILE = "/root/proxies.txt"
DB_FILE = "/root/proxy_panel_db.json"
CONFIG_FILE = "/root/proxy_panel_config.json"

CHECK_TEST_URL = "https://api.ipify.org"
CHECK_TIMEOUT = 15  # seconds

app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET", "change-me-please")


# -------------
# Utils
# -------------

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def ensure_config():
    cfg = load_json(CONFIG_FILE, {})
    changed = False
    if "admin_username" not in cfg:
        cfg["admin_username"] = "admin"
        changed = True
    if "password_hash" not in cfg:
        cfg["password_hash"] = generate_password_hash("admin")
        changed = True
    if changed:
        save_json(CONFIG_FILE, cfg)
    return cfg


def get_config():
    return ensure_config()


def set_admin_credentials(new_username, new_password):
    cfg = get_config()
    if new_username:
        cfg["admin_username"] = new_username
    if new_password:
        cfg["password_hash"] = generate_password_hash(new_password)
    save_json(CONFIG_FILE, cfg)


def ensure_db():
    db = load_json(DB_FILE, {})
    changed = False
    if "clients" not in db:
        db["clients"] = []
        changed = True
    if "proxy_checks" not in db:
        db["proxy_checks"] = {}  # proxy_line -> {status, last_ok, last_error}
        changed = True

    # Migration: transform old clients with explicit proxies into "count"-based ones
    for c in db["clients"]:
        if "count" not in c:
            if "proxies" in c and isinstance(c["proxies"], list):
                c["count"] = len(c["proxies"])
            else:
                c["count"] = 0
            changed = True
        # ensure integer
        try:
            c["count"] = int(c["count"])
        except Exception:
            c["count"] = 0
            changed = True
        # ensure login/password keys exist
        if "login" not in c:
            c["login"] = ""
            changed = True
        if "password" not in c:
            c["password"] = ""
            changed = True
        # on garde éventuellement "proxies" mais on ne s'en sert plus pour l'assignation

    if changed:
        save_json(DB_FILE, db)
    return db


def get_db():
    return ensure_db()


def save_db(db):
    save_json(DB_FILE, db)


def load_proxy_list():
    try:
        with open(PROXY_SOURCE_FILE, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.readlines()]
        return [ln for ln in lines if ln]
    except FileNotFoundError:
        return []


def next_client_id(db):
    if not db["clients"]:
        return 1
    return max(c["id"] for c in db["clients"]) + 1


def compute_client_ranges(db, proxies):
    """
    For each client, compute slice [start, end) of the proxy list,
    based on ascending client ID and count.
    """
    clients_sorted = sorted(db["clients"], key=lambda x: x["id"])
    ranges = {}
    idx = 0
    for c in clients_sorted:
        count = max(0, int(c.get("count", 0)))
        start = idx
        end = start + count
        ranges[c["id"]] = (start, end)
        idx = end
    return ranges


def get_client_proxies(client, db=None, proxies=None):
    if db is None:
        db = get_db()
    if proxies is None:
        proxies = load_proxy_list()
    ranges = compute_client_ranges(db, proxies)
    start, end = ranges.get(client["id"], (0, 0))
    return proxies[start:end]


def build_assignment_map(db=None, proxies=None):
    if db is None:
        db = get_db()
    if proxies is None:
        proxies = load_proxy_list()
    ranges = compute_client_ranges(db, proxies)
    mapping = {}
    for cid, (start, end) in ranges.items():
        for line in proxies[start:end]:
            mapping[line] = cid
    return mapping


def compute_stats():
    db = get_db()
    proxies = load_proxy_list()
    total = len(proxies)
    total_assigned = sum(max(0, int(c.get("count", 0))) for c in db["clients"])
    assigned = min(total, total_assigned)
    available = max(0, total - total_assigned)
    return {
        "total_proxies": total,
        "assigned_proxies": assigned,
        "available_proxies": available,
        "clients_count": len(db["clients"]),
        "proxy_source": PROXY_SOURCE_FILE,
        "db_file": DB_FILE,
    }


# -------------
# Auth decorators
# -------------

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def client_login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("client_logged_in"):
            return redirect(url_for("client_login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


# -------------
# Templates
# -------------

BASE_CSS = """
:root {
  --bg: #020617;
  --bg-alt: #020617;
  --card: #020617;
  --border-subtle: rgba(31,41,55,0.9);
  --accent: #22c55e;
  --accent-soft: rgba(34,197,94,0.15);
  --accent-strong: #22c55e;
  --danger: #ef4444;
  --danger-soft: rgba(248,113,113,0.16);
  --danger-strong: #ef4444;
  --text: #e5e7eb;
  --text-muted: #9ca3af;
  --text-soft: #6b7280;
  --badge-bg: rgba(31,41,55,0.9);
}
*,
*::before,
*::after {
  box-sizing: border-box;
}
html, body {
  margin:0;
  padding:0;
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "SF Pro Text",
               "Segoe UI", sans-serif;
  background: radial-gradient(circle at top, #0f172a 0, #020617 52%, #000 100%);
  color: var(--text);
}
body {
  min-height:100vh;
  display:flex;
  align-items:stretch;
  justify-content:center;
}
.app-frame {
  width:100%;
  max-width:1100px;
  margin:24px auto;
  padding:20px;
}
.card {
  border-radius:22px;
  border:1px solid var(--border-subtle);
  background: radial-gradient(circle at top left, rgba(15,23,42,0.9) 0, #020617 55%);
  box-shadow:
    0 18px 60px rgba(15,23,42,0.75),
    0 0 0 1px rgba(15,23,42,0.6);
  padding:20px 22px;
}
.header {
  display:flex;
  justify-content:space-between;
  gap:16px;
  align-items:flex-start;
  margin-bottom:18px;
}
.brand-title {
  font-size:18px;
  font-weight:600;
  letter-spacing:0.03em;
}
.brand-sub {
  margin-top:4px;
  font-size:12px;
  color:var(--text-muted);
}
.badge {
  display:inline-flex;
  align-items:center;
  gap:6px;
  border-radius:999px;
  padding:3px 10px;
  font-size:11px;
  border:1px solid rgba(55,65,81,0.85);
  background:linear-gradient(120deg, rgba(15,23,42,0.85), rgba(15,23,42,0.65));
  color:var(--text-soft);
}
.badge-dot {
  width:5px;
  height:5px;
  border-radius:999px;
  background:var(--accent);
  box-shadow:0 0 10px rgba(34,197,94,0.6);
}
.nav {
  display:flex;
  gap:10px;
  align-items:center;
}
.nav a,
.nav span.nav-link {
  font-size:12px;
  padding:6px 10px;
  border-radius:999px;
  border:1px solid transparent;
  color:var(--text-muted);
  text-decoration:none;
}
.nav a.active,
.nav span.nav-link.active {
  border-color:rgba(148,163,184,0.5);
  background:rgba(15,23,42,0.8);
  color:var(--text);
}
.nav a.logout {
  color:var(--danger-strong);
}
.main-grid {
  display:grid;
  grid-template-columns:2.1fr 1.1fr;
  gap:16px;
}
@media (max-width: 900px) {
  .main-grid {
    grid-template-columns:1fr;
  }
}
.section-title {
  font-size:15px;
  font-weight:500;
  margin-bottom:8px;
}
.muted {
  color:var(--text-muted);
  font-size:12px;
}
.stats-grid {
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:10px;
}
.stat-card {
  padding:10px 12px;
  border-radius:16px;
  border:1px solid rgba(31,41,55,0.9);
  background:radial-gradient(circle at top, rgba(15,23,42,0.85), #020617 60%);
}
.stat-label {
  font-size:11px;
  color:var(--text-soft);
}
.stat-value {
  font-size:18px;
  font-weight:600;
  margin-top:2px;
}
.stat-hint {
  font-size:10px;
  color:var(--text-muted);
  margin-top:4px;
}
.form-grid {
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:10px;
}
.form-row {
  display:flex;
  flex-direction:column;
  gap:4px;
  margin-bottom:6px;
}
.form-row label {
  font-size:11px;
  color:var(--text-soft);
}
.input {
  border-radius:10px;
  border:1px solid rgba(51,65,85,0.9);
  background:#020617;
  color:#e5e7eb;
  padding:7px 9px;
  font-size:13px;
}
.input::placeholder {
  color:#4b5563;
}
.button {
  display:inline-flex;
  align-items:center;
  justify-content:center;
  gap:6px;
  border-radius:999px;
  border:none;
  padding:7px 14px;
  font-size:12px;
  cursor:pointer;
  background:linear-gradient(120deg, #22c55e, #16a34a);
  color:#0f172a;
  font-weight:500;
}
.button.secondary {
  background:rgba(15,23,42,1);
  color:var(--text);
  border:1px solid rgba(75,85,99,0.8);
}
.button.small {
  padding:4px 9px;
  font-size:11px;
}
.button.danger {
  background:var(--danger-soft);
  color:var(--danger-strong);
  border:1px solid rgba(248,113,113,0.7);
}
.error {
  border-radius:10px;
  padding:8px 10px;
  background:var(--danger-soft);
  border:1px solid rgba(248,113,113,0.4);
  color:var(--danger-strong);
  font-size:12px;
  margin-bottom:8px;
}
.table-wrapper {
  margin-top:6px;
  border-radius:18px;
  border:1px solid rgba(31,41,55,0.9);
  background:radial-gradient(circle at top, rgba(15,23,42,0.8), #020617 60%);
  max-height:460px;
  overflow:auto;
}
table {
  border-collapse:collapse;
  width:100%;
  font-size:12px;
}
th, td {
  padding:6px 8px;
  border-bottom:1px solid rgba(31,41,55,0.9);
}
th {
  text-align:left;
  position:sticky;
  top:0;
  background:rgba(15,23,42,0.98);
  z-index:2;
}
tr:last-child td {
  border-bottom:none;
}
.status-badge {
  display:inline-flex;
  align-items:center;
  justify-content:center;
  min-width:56px;
  padding:2px 8px;
  border-radius:999px;
  font-size:11px;
}
.status-unknown {
  background:rgba(31,41,55,0.9);
  color:var(--text-soft);
}
.status-ok {
  background:var(--accent-soft);
  color:var(--accent-strong);
}
.status-fail {
  background:var(--danger-soft);
  color:var(--danger-strong);
}
.footer-note {
  margin-top:10px;
  font-size:10px;
  color:var(--text-soft);
  text-align:right;
}
textarea {
  width:100%;
  border-radius:12px;
  border:1px solid rgba(31,41,55,0.9);
  background:#020617;
  color:#e5e7eb;
  padding:8px 10px;
  font-size:12px;
}
"""

LOGIN_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{{ title }} – Login</title>
  <style>{{ css }}</style>
</head>
<body>
  <div class="app-frame">
    <div class="card" style="max-width:440px;margin:40px auto;">
      <div class="header">
        <div>
          <div class="badge">
            <span class="badge-dot"></span>
            {{ login_badge or 'Admin console' }}
          </div>
          <div class="brand-title" style="margin-top:6px;">{{ title }}</div>
          <div class="brand-sub">
            {{ login_subtitle or 'Secure access to your proxy management dashboard.' }}
          </div>
        </div>
      </div>

      {% if error %}
        <div class="error">{{ error }}</div>
      {% endif %}

      <form method="post">
        <div class="form-row">
          <label>Username</label>
          <input class="input" type="text" name="username" value="{{ default_user or '' }}" autocomplete="username">
        </div>
        <div class="form-row">
          <label>Password</label>
          <input class="input" type="password" name="password" autocomplete="current-password">
        </div>
        <button class="button" type="submit">Sign in</button>
      </form>
    </div>
  </div>
</body>
</html>
"""

LAYOUT_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{{ title }} – {{ page_title }}</title>
  <style>{{ css }}</style>
</head>
<body>
  <div class="app-frame">
    <div class="card">
      <div class="header">
        <div>
          <div class="badge">
            <span class="badge-dot"></span>
            {% if is_client %}
              Client area
            {% else %}
              Admin console
            {% endif %}
          </div>
          <div class="brand-title" style="margin-top:6px;">
            {{ title }} PANEL
          </div>
          <div class="brand-sub">
            {% if is_client %}
              Access your dedicated proxy list.
            {% else %}
              Internal proxy management console.
            {% endif %}
          </div>
        </div>
        <div class="nav">
          {% if is_client %}
            <span class="nav-link active">My proxies</span>
            <a class="nav-link logout" href="{{ url_for('client_logout') }}">Logout</a>
          {% else %}
            <a class="nav-link {% if active=='dashboard' %}active{% endif %}" href="{{ url_for('dashboard') }}">Dashboard</a>
            <a class="nav-link {% if active=='clients' %}active{% endif %}" href="{{ url_for('clients') }}">Clients</a>
            <a class="nav-link {% if active=='proxies' %}active{% endif %}" href="{{ url_for('proxies') }}">Proxies</a>
            <a class="nav-link {% if active=='settings' %}active{% endif %}" href="{{ url_for('settings') }}">Settings</a>
            <a class="nav-link logout" href="{{ url_for('logout') }}">Logout</a>
          {% endif %}
        </div>
      </div>

      <div class="main-grid">
        <div>
          {{ body|safe }}
        </div>
        <div>
          <div class="section-title">Stats</div>
          <div class="stats-grid">
            <div class="stat-card">
              <div class="stat-label">Total proxies</div>
              <div class="stat-value">{{ stats.total_proxies }}</div>
              <div class="stat-hint">From {{ stats.proxy_source }}</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">Assigned to clients</div>
              <div class="stat-value">{{ stats.assigned_proxies }}</div>
              <div class="stat-hint">{{ stats.clients_count }} clients</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">Available</div>
              <div class="stat-value">{{ stats.available_proxies }}</div>
              <div class="stat-hint">Not yet allocated</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">DB file</div>
              <div class="stat-value" style="font-size:11px;word-break:break-all;">
                {{ stats.db_file }}
              </div>
              <div class="stat-hint">JSON storage</div>
            </div>
          </div>
        </div>
      </div>

      <div class="footer-note">
        {% if is_client %}
          Client panel
        {% else %}
          Admin panel · Proxy source: {{ stats.proxy_source }} · DB: {{ stats.db_file }}
        {% endif %}
      </div>
    </div>
  </div>
</body>
</html>
"""


# -------------
# Auth routes
# -------------

@app.route("/login", methods=["GET", "POST"])
def login():
    cfg = get_config()
    error = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if username == cfg["admin_username"] and check_password_hash(cfg["password_hash"], password):
            session.clear()
            session["admin_logged_in"] = True
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
        else:
            error = "Invalid username or password."

    return render_template_string(
        LOGIN_TEMPLATE,
        title=APP_TITLE,
        css=BASE_CSS,
        error=error,
        default_user=cfg["admin_username"],
        login_badge="Admin console",
        login_subtitle="Secure access to your proxy management dashboard.",
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/client/login", methods=["GET", "POST"])
def client_login():
    db = get_db()
    error = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        client = next(
            (c for c in db["clients"] if c.get("login") == username and c.get("password") == password),
            None,
        )
        if client:
            session.clear()
            session["client_logged_in"] = True
            session["client_id"] = client["id"]
            next_url = request.args.get("next") or url_for("client_dashboard")
            return redirect(next_url)
        else:
            error = "Invalid username or password."

    return render_template_string(
        LOGIN_TEMPLATE,
        title=APP_TITLE,
        css=BASE_CSS,
        error=error,
        default_user="",
        login_badge="Client area",
        login_subtitle="Access your dedicated proxy list.",
    )


@app.route("/client/logout")
def client_logout():
    session.clear()
    return redirect(url_for("client_login"))


# -------------
# Admin pages
# -------------

@app.route("/")
@login_required
def root():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
@login_required
def dashboard():
    stats = compute_stats()
    db = get_db()

    body = render_template_string(
        """
        <div class="section-title">Overview</div>
        <p class="muted" style="margin-bottom:10px;">
          Central panel to allocate proxies to clients and monitor basic health checks.
        </p>
        <div class="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Client</th>
                <th>Login</th>
                <th>Proxies</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {% for c in clients %}
                <tr>
                  <td>#{{ c.id }}</td>
                  <td>{{ c.name }}</td>
                  <td>{{ c.login or '-' }}</td>
                  <td>{{ c.count }}</td>
                  <td>{{ c.created_at }}</td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
        """,
        clients=sorted(db["clients"], key=lambda x: x["id"]),
    )

    return render_template_string(
        LAYOUT_TEMPLATE,
        title=APP_TITLE,
        css=BASE_CSS,
        page_title="Dashboard",
        active="dashboard",
        body=body,
        stats=stats,
        is_client=False,
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
        login_name = request.form.get("login", "").strip()
        password = request.form.get("password", "")

        try:
            count = int(count_str)
        except ValueError:
            count = 0

        if not name:
            error = "Client name is required."
        elif not login_name:
            error = "Client login is required."
        elif any(c.get("login") == login_name for c in db["clients"]):
            error = "This client login is already used."
        elif not password:
            error = "Client password is required."
        elif count <= 0:
            error = "Number of proxies must be a positive integer."
        else:
            all_proxies = load_proxy_list()
            already_assigned = sum(max(0, int(c.get("count", 0))) for c in db["clients"])
            remaining = len(all_proxies) - already_assigned
            if remaining < count:
                error = f"Not enough available proxies. Requested {count}, only {max(0, remaining)} left."
            else:
                client_id = next_client_id(db)
                created_at = datetime.datetime.now().isoformat(timespec="seconds")
                client = {
                    "id": client_id,
                    "name": name,
                    "count": count,
                    "login": login_name,
                    "password": password,
                    "created_at": created_at,
                }
                db["clients"].append(client)
                save_db(db)
                return redirect(url_for("clients"))

    clients_sorted = sorted(db["clients"], key=lambda x: x["id"])

    body = render_template_string(
        """
        <div class="section-title">Create new client</div>
        {% if error %}
          <div class="error">{{ error }}</div>
        {% endif %}
        <form method="post" style="margin-bottom:14px;">
          <div class="form-grid">
            <div class="form-row">
              <label>Client name</label>
              <input class="input" type="text" name="name" placeholder="e.g. Mohamed">
            </div>
            <div class="form-row">
              <label>Number of proxies</label>
              <input class="input" type="number" name="count" min="1" placeholder="100">
            </div>
          </div>
          <div class="form-grid">
            <div class="form-row">
              <label>Client login</label>
              <input class="input" type="text" name="login" placeholder="e.g. client123">
            </div>
            <div class="form-row">
              <label>Client password</label>
              <input class="input" type="text" name="password" placeholder="set a password">
            </div>
          </div>
          <button class="button" type="submit">Create client</button>
        </form>

        <div class="section-title">Existing clients</div>
        <div class="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Login</th>
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
                  <td>{{ c.login or '-' }}</td>
                  <td>{{ c.count }}</td>
                  <td>{{ c.created_at }}</td>
                  <td>
                    <a class="button small secondary" href="{{ url_for('download_client', client_id=c.id) }}">Download</a>
                    <form method="post" action="{{ url_for('delete_client', client_id=c.id) }}" style="display:inline;margin-left:4px;" onsubmit="return confirm('Delete this client?');">
                      <button class="button small danger" type="submit">Delete</button>
                    </form>
                  </td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
        """,
        clients=clients_sorted,
        error=error,
    )

    return render_template_string(
        LAYOUT_TEMPLATE,
        title=APP_TITLE,
        css=BASE_CSS,
        page_title="Clients",
        active="clients",
        body=body,
        stats=stats,
        is_client=False,
    )


@app.route("/clients/<int:client_id>/download")
@login_required
def download_client(client_id):
    db = get_db()
    client = next((c for c in db["clients"] if c["id"] == client_id), None)
    if not client:
        abort(404)
    proxies = get_client_proxies(client, db=db)
    filename = f"{client['name']}_{client['count']}proxies.txt"
    content = "\n".join(proxies) + ("\n" if proxies else "")
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
    before = len(db["clients"])
    db["clients"] = [c for c in db["clients"] if c["id"] != client_id]
    after = len(db["clients"])
    if after != before:
        save_db(db)
    return redirect(url_for("clients"))


@app.route("/proxies")
@login_required
def proxies():
    stats = compute_stats()
    db = get_db()
    proxies_list = load_proxy_list()
    assigned_map = build_assignment_map(db, proxies_list)
    checks = db.get("proxy_checks", {})

    table_data = []
    for line in proxies_list:
        client_id = assigned_map.get(line)
        client_name = None
        if client_id is not None:
            c = next((c for c in db["clients"] if c["id"] == client_id), None)
            if c:
                client_name = c["name"]
        check_info = checks.get(line, {})
        status = check_info.get("status", "unknown")
        last_error = check_info.get("last_error", "")
        table_data.append(
            {
                "proxy": line,
                "client_id": client_id,
                "client_name": client_name,
                "status": status,
                "last_error": last_error,
            }
        )

    body = render_template_string(
        """
        <div class="section-title">All proxies</div>
        <p class="muted" style="margin-bottom:8px;">
          Proxies are read from <code>{{ proxy_source }}</code>. Click "Check ALL Proxies" to run a simple connectivity test via each proxy.
        </p>
        <button class="button small" id="check-all">Check ALL Proxies</button>
        <div id="check-summary" class="muted" style="margin-top:6px;font-size:11px;"></div>
        <div class="table-wrapper" style="margin-top:8px;">
          <table id="proxy-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Proxy</th>
                <th>Client</th>
                <th>Status</th>
                <th>Last error</th>
              </tr>
            </thead>
            <tbody>
              {% for row in rows %}
                <tr data-proxy="{{ row.proxy }}">
                  <td>{{ loop.index }}</td>
                  <td style="font-family:monospace;">{{ row.proxy }}</td>
                  <td>
                    {% if row.client_id %}
                      #{{ row.client_id }} – {{ row.client_name or 'Client' }}
                    {% else %}
                      <span class="muted">Unassigned</span>
                    {% endif %}
                  </td>
                  <td>
                    {% if row.status == 'ok' %}
                      <span class="status-badge status-ok">OK</span>
                    {% elif row.status == 'fail' %}
                      <span class="status-badge status-fail">FAIL</span>
                    {% else %}
                      <span class="status-badge status-unknown">UNKNOWN</span>
                    {% endif %}
                  </td>
                  <td style="font-size:11px;color:var(--text-soft);">
                    {{ row.last_error or '' }}
                  </td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>

        <script>
        const checkBtn = document.getElementById('check-all');
        const table = document.getElementById('proxy-table');
        const summary = document.getElementById('check-summary');

        async function checkProxy(row) {
          const proxy = row.getAttribute('data-proxy');
          const statusCell = row.querySelector('td:nth-child(4)');
          const errorCell = row.querySelector('td:nth-child(5)');
          statusCell.innerHTML = '<span class="status-badge status-unknown">...</span>';
          errorCell.textContent = '';
          try {
            const resp = await fetch('{{ url_for("api_check_proxy") }}', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ proxy })
            });
            const data = await resp.json();
            if (data.ok) {
              statusCell.innerHTML = '<span class="status-badge status-ok">OK</span>';
              errorCell.textContent = '';
            } else {
              statusCell.innerHTML = '<span class="status-badge status-fail">FAIL</span>';
              errorCell.textContent = data.error || 'error';
            }
            return data.ok;
          } catch (e) {
            statusCell.innerHTML = '<span class="status-badge status-fail">FAIL</span>';
            errorCell.textContent = 'request error';
            return false;
          }
        }

        checkBtn?.addEventListener('click', async () => {
          const rows = Array.from(table.querySelectorAll('tbody tr'));
          let okCount = 0;
          let failCount = 0;
          summary.textContent = 'Checking...';
          for (const row of rows) {
            const ok = await checkProxy(row);
            if (ok) okCount++; else failCount++;
          }
          summary.textContent = `Done. OK: ${okCount}, FAIL: ${failCount}`;
        });
        </script>
        """,
        rows=table_data,
        proxy_source=PROXY_SOURCE_FILE,
    )

    return render_template_string(
        LAYOUT_TEMPLATE,
        title=APP_TITLE,
        css=BASE_CSS,
        page_title="Proxies",
        active="proxies",
        body=body,
        stats=stats,
        is_client=False,
    )


@app.route("/api/check_proxy", methods=["POST"])
@login_required
def api_check_proxy():
    data = request.get_json(force=True, silent=True) or {}
    proxy_line = data.get("proxy", "").strip()
    if not proxy_line:
        return jsonify({"ok": False, "error": "missing proxy"})
    proxies = {
        "http": f"http://{proxy_line}",
        "https": f"http://{proxy_line}",
    }
    ok = False
    error_msg = ""
    try:
        resp = requests.get(
            CHECK_TEST_URL,
            proxies=proxies,
            timeout=CHECK_TIMEOUT,
        )
        if resp.status_code == 200:
            ok = True
        else:
            error_msg = f"HTTP {resp.status_code}"
    except Exception as e:
        error_msg = str(e)[:120]

    db = get_db()
    checks = db.setdefault("proxy_checks", {})
    checks[proxy_line] = {
        "status": "ok" if ok else "fail",
        "last_ok": datetime.datetime.now().isoformat(timespec="seconds") if ok else checks.get(proxy_line, {}).get("last_ok"),
        "last_error": "" if ok else error_msg,
    }
    save_db(db)
    return jsonify({"ok": ok, "error": error_msg})


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    stats = compute_stats()
    cfg = get_config()
    error = None
    success = None

    if request.method == "POST":
        new_user = request.form.get("admin_username", "").strip()
        new_pass = request.form.get("admin_password", "").strip()
        if not new_user and not new_pass:
            error = "Nothing to update."
        else:
            set_admin_credentials(new_user or None, new_pass or None)
            success = "Credentials updated successfully."

    cfg = get_config()

    body = render_template_string(
        """
        <div class="section-title">Admin settings</div>
        {% if error %}
          <div class="error">{{ error }}</div>
        {% endif %}
        {% if success %}
          <div class="error" style="background:var(--accent-soft);border-color:rgba(34,197,94,0.5);color:var(--accent-strong);">
            {{ success }}
          </div>
        {% endif %}
        <form method="post" style="max-width:360px;">
          <div class="form-row">
            <label>Admin username</label>
            <input class="input" type="text" name="admin_username" value="{{ cfg.admin_username }}">
          </div>
          <div class="form-row">
            <label>New admin password</label>
            <input class="input" type="password" name="admin_password" placeholder="Leave empty to keep current">
          </div>
          <button class="button" type="submit">Update</button>
        </form>

        <div style="margin-top:16px;">
          <div class="section-title">Info</div>
          <p class="muted">
            Admin panel login URL: <code>{{ url_for('login', _external=True) }}</code><br>
            Client panel login URL: <code>{{ url_for('client_login', _external=True) }}</code>
          </p>
        </div>
        """,
        cfg=cfg,
        error=error,
        success=success,
    )

    return render_template_string(
        LAYOUT_TEMPLATE,
        title=APP_TITLE,
        css=BASE_CSS,
        page_title="Settings",
        active="settings",
        body=body,
        stats=stats,
        is_client=False,
    )


# -------------
# Client page
# -------------

@app.route("/client/dashboard")
@client_login_required
def client_dashboard():
    stats = compute_stats()
    db = get_db()
    cid = session.get("client_id")
    client = next((c for c in db["clients"] if c["id"] == cid), None)
    if not client:
        return redirect(url_for("client_logout"))
    proxies = get_client_proxies(client, db=db)

    body = render_template_string(
        """
        <div class="section-title">My proxies</div>
        <p class="muted" style="margin-bottom:6px;">
          Logged in as <strong>{{ client.name }}</strong> ({{ client.login }}).
        </p>
        {% if proxies %}
          <label style="font-size:11px;color:var(--text-soft);margin-bottom:3px;display:block;">
            Copy / paste your proxies ({{ proxies|length }}):
          </label>
          <textarea rows="14" readonly>{% for p in proxies %}{{ p }}{% if not loop.last %}&#10;{% endif %}{% endfor %}</textarea>
        {% else %}
          <p class="muted">No proxies assigned yet. Please contact support.</p>
        {% endif %}
        """,
        client=client,
        proxies=proxies,
    )

    return render_template_string(
        LAYOUT_TEMPLATE,
        title=APP_TITLE,
        css=BASE_CSS,
        page_title="My proxies",
        active="client",
        body=body,
        stats=stats,
        is_client=True,
    )


def main():
    parser = argparse.ArgumentParser(description="Proxy panel (admin + client)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=1991)
    args = parser.parse_args()

    ensure_config()
    ensure_db()

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
