#!/usr/bin/env python3
"""
Simple proxy management panel with login and modern-looking UI.

- Admin login:
    username: admin
    password: lolopolo

- Reads proxies from /root/proxies.txt  (one proxy per line)
- Stores assignments and history in /root/proxy_panel_db.json

Run:
    python3 proxy_panel.py --host 0.0.0.0 --port 1991
"""

import argparse
import json
import math
import os
from datetime import datetime

from flask import (
    Flask,
    Response,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

APP_TITLE = "Proxy Panel"
ADMIN_USER = "admin"
ADMIN_PASS = "lolopolo"

PROXY_FILE = "/root/proxies.txt"
DB_FILE = "/root/proxy_panel_db.json"

SECRET_KEY = "change-this-secret-key"  # you can change this if you want

app = Flask(__name__)
app.secret_key = SECRET_KEY


# ---------- Helpers ----------


def load_proxies():
    """Read master proxy list from PROXY_FILE."""
    proxies = []
    if os.path.exists(PROXY_FILE):
        with open(PROXY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                proxies.append(line)
    return proxies


def default_db():
    return {"clients": [], "assigned": {}, "history": []}


def load_db():
    if not os.path.exists(DB_FILE):
        return default_db()
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "clients" not in data or "assigned" not in data or "history" not in data:
            return default_db()
        return data
    except Exception:
        return default_db()


def save_db(db):
    tmp = DB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2)
    os.replace(tmp, DB_FILE)


def require_login():
    return session.get("logged_in") is True


@app.before_request
def _check_login():
    if request.endpoint in ("login", "static"):
        return
    if not require_login():
        return redirect(url_for("login", next=request.path))


def render_page(title, active_tab, body_html, extra=None):
    if extra is None:
        extra = {}
    base_template = """
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <title>{{ title }}</title>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <style>
        :root {
          --bg-dark: #050816;
          --bg-card: #111827;
          --bg-card-soft: #0b1220;
          --accent-blue: #3b82f6;
          --accent-pink: #ec4899;
          --accent-green: #22c55e;
          --accent-red: #ef4444;
          --accent-orange: #f97316;
          --accent-yellow: #eab308;
          --text-main: #e5e7eb;
          --text-muted: #9ca3af;
          --border-soft: #1f2937;
        }
        * { box-sizing: border-box; margin:0; padding:0; }
        body {
          font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: radial-gradient(circle at top left, #1d2548 0, var(--bg-dark) 55%);
          color: var(--text-main);
          min-height: 100vh;
        }
        a { color: inherit; text-decoration: none; }
        .layout {
          max-width: 1300px;
          margin: 0 auto;
          padding: 16px 20px 32px;
        }
        .topbar {
          display:flex;
          justify-content: space-between;
          align-items:center;
          margin-bottom: 20px;
        }
        .logo {
          font-weight: 700;
          letter-spacing: .08em;
          font-size: 14px;
          text-transform: uppercase;
          display:flex;
          align-items:center;
          gap:8px;
        }
        .logo-dot {
          width:10px; height:10px; border-radius:999px;
          background: linear-gradient(135deg, var(--accent-blue), var(--accent-pink));
        }
        .nav {
          display:flex; gap:10px;
        }
        .nav a {
          font-size: 13px;
          padding: 6px 14px;
          border-radius: 999px;
          color: var(--text-muted);
          border:1px solid transparent;
          background: rgba(15,23,42,.7);
        }
        .nav a.active {
          color:white;
          border-color:rgba(148,163,184,.4);
          background: radial-gradient(circle at top, rgba(59,130,246,.35), rgba(15,23,42,1));
        }
        .nav a:hover { border-color:rgba(148,163,184,.5); }
        .logout-btn {
          font-size:12px;
          padding:5px 12px;
          border-radius:999px;
          border:1px solid rgba(239,68,68,.7);
          color:#fecaca;
          background:rgba(127,29,29,.6);
        }
        .logout-btn:hover {
          background:rgba(239,68,68,.9);
          color:white;
        }
        .grid {
          display:grid;
          grid-template-columns: repeat(auto-fit, minmax(260px,1fr));
          gap:16px;
          margin-bottom: 24px;
        }
        .card {
          background: radial-gradient(circle at top left, rgba(59,130,246,.15), rgba(15,23,42,1));
          border-radius:16px;
          padding:16px 18px;
          border:1px solid rgba(148,163,184,.35);
          box-shadow:0 18px 45px rgba(15,23,42,.9);
        }
        .card h3 {
          font-size:13px;
          text-transform:uppercase;
          letter-spacing:.12em;
          color:var(--text-muted);
          margin-bottom:10px;
        }
        .card-main {
          display:flex;
          align-items:baseline;
          gap:8px;
          margin-bottom:6px;
        }
        .card-main span.value {
          font-size:26px;
          font-weight:600;
        }
        .card-main span.label {
          font-size:11px;
          text-transform:uppercase;
          letter-spacing:.14em;
          color:var(--text-muted);
        }
        .pill {
          display:inline-flex;
          align-items:center;
          gap:6px;
          padding:4px 9px;
          border-radius:999px;
          font-size:11px;
          border:1px solid rgba(148,163,184,.4);
          color:var(--text-muted);
        }
        .pill-dot {
          width:7px; height:7px; border-radius:999px;
        }
        .pill-dot.green { background:var(--accent-green); }
        .pill-dot.red { background:var(--accent-red); }
        .pill-dot.blue { background:var(--accent-blue); }
        .pill-dot.orange { background:var(--accent-orange); }
        .pill-dot.yellow { background:var(--accent-yellow); }
        .section {
          margin-top: 8px;
          background: linear-gradient(145deg, var(--bg-card-soft), #020617);
          border-radius:18px;
          border:1px solid rgba(31,41,55,.9);
          padding:16px 18px 18px;
          box-shadow:0 18px 40px rgba(15,23,42,.9);
        }
        .section-header {
          display:flex;
          justify-content:space-between;
          align-items:center;
          margin-bottom:12px;
        }
        .section-title {
          font-size:14px;
          font-weight:600;
        }
        .section-sub {
          font-size:12px;
          color:var(--text-muted);
        }
        table {
          width:100%;
          border-collapse:collapse;
          font-size:12px;
          margin-top:6px;
        }
        th, td {
          padding:7px 8px;
          text-align:left;
        }
        thead {
          background:rgba(15,23,42,.9);
        }
        tbody tr:nth-child(even) {
          background:rgba(15,23,42,.55);
        }
        tbody tr:nth-child(odd) {
          background:rgba(15,23,42,.9);
        }
        th {
          font-weight:500;
          color:var(--text-muted);
          border-bottom:1px solid var(--border-soft);
        }
        td {
          border-bottom:1px solid rgba(15,23,42,1);
        }
        .badge {
          display:inline-flex;
          align-items:center;
          padding:2px 7px;
          border-radius:999px;
          font-size:11px;
        }
        .badge.green { background:rgba(22,163,74,.18); color:#bbf7d0; }
        .badge.red { background:rgba(220,38,38,.2); color:#fecaca; }
        .badge.blue { background:rgba(59,130,246,.22); color:#bfdbfe; }
        .badge.pink { background:rgba(236,72,153,.22); color:#f9a8d4; }
        .btn {
          display:inline-flex;
          align-items:center;
          justify-content:center;
          padding:6px 11px;
          border-radius:999px;
          font-size:11px;
          border:1px solid transparent;
          cursor:pointer;
          background:rgba(15,23,42,.8);
          color:var(--text-main);
        }
        .btn.primary {
          background:linear-gradient(135deg, var(--accent-blue), var(--accent-pink));
          border:none;
          color:white;
        }
        .btn.small { padding:4px 9px; font-size:11px; }
        .btn.outline {
          border-color:rgba(148,163,184,.7);
        }
        .btn + .btn { margin-left:6px; }
        .btn:hover {
          filter:brightness(1.1);
        }
        form.inline { display:inline; }
        .form-grid {
          display:grid;
          grid-template-columns: repeat(auto-fit,minmax(220px,1fr));
          gap:14px;
          margin-top:10px;
        }
        label {
          font-size:12px;
          display:block;
          margin-bottom:4px;
          color:var(--text-muted);
        }
        input[type="text"], input[type="number"], input[type="password"] {
          width:100%;
          padding:7px 9px;
          border-radius:10px;
          border:1px solid rgba(55,65,81,.8);
          background:rgba(15,23,42,.9);
          color:var(--text-main);
          font-size:13px;
        }
        input:focus {
          outline:none;
          border-color:var(--accent-blue);
          box-shadow:0 0 0 1px rgba(59,130,246,.7);
        }
        .message {
          margin-top:6px;
          font-size:12px;
          color:#fca5a5;
        }
        .success {
          color:#bbf7d0;
        }
        .pagination {
          margin-top:10px;
          font-size:12px;
          display:flex;
          justify-content:flex-end;
          gap:8px;
        }
        .pagination a {
          padding:3px 8px;
          border-radius:999px;
          border:1px solid rgba(75,85,99,.9);
          color:var(--text-muted);
        }
        .pagination span.current {
          padding:3px 8px;
          border-radius:999px;
          background:rgba(59,130,246,.4);
        }
        .login-wrapper {
          display:flex;
          align-items:center;
          justify-content:center;
          min-height:100vh;
          padding:24px;
        }
        .login-card {
          width:360px;
          max-width:100%;
          background:radial-gradient(circle at top, rgba(59,130,246,.25), #020617);
          border-radius:18px;
          padding:22px 22px 20px;
          border:1px solid rgba(148,163,184,.4);
          box-shadow:0 22px 45px rgba(15,23,42,.95);
        }
        .login-title {
          font-size:18px;
          font-weight:600;
          margin-bottom:4px;
        }
        .login-sub { font-size:12px; color:var(--text-muted); margin-bottom:16px; }
        .footer {
          margin-top:18px;
          text-align:center;
          font-size:11px;
          color:var(--text-muted);
        }
        @media (max-width:640px){
          .topbar { flex-direction:column; align-items:flex-start; gap:10px; }
          .nav { flex-wrap:wrap; }
        }
      </style>
    </head>
    <body>
      {% if not logged_in %}
        <div class="login-wrapper">
          <div class="login-card">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
              <div class="logo-dot"></div>
              <div>
                <div class="login-title">Proxy Panel</div>
                <div class="login-sub">Local admin login</div>
              </div>
            </div>
            {{ body|safe }}
            <div class="footer">Default admin: <span style="color:#e5e7eb;">admin / lolopolo</span></div>
          </div>
        </div>
      {% else %}
        <div class="layout">
          <div class="topbar">
            <div class="logo">
              <div class="logo-dot"></div>
              <span>PROXY PANEL</span>
            </div>
            <div style="display:flex;align-items:center;gap:10px;">
              <nav class="nav">
                <a href="{{ url_for('dashboard') }}" class="{% if active_tab=='overview' %}active{% endif %}">Overview</a>
                <a href="{{ url_for('proxies_view') }}" class="{% if active_tab=='proxies' %}active{% endif %}">Proxies</a>
                <a href="{{ url_for('clients_view') }}" class="{% if active_tab=='clients' %}active{% endif %}">Clients</a>
                <a href="{{ url_for('history_view') }}" class="{% if active_tab=='history' %}active{% endif %}">History</a>
              </nav>
              <form method="post" action="{{ url_for('logout') }}">
                <button class="logout-btn" type="submit">Logout</button>
              </form>
            </div>
          </div>
          {{ body|safe }}
          <div class="footer">Simple proxy management panel · local use only</div>
        </div>
      {% endif %}
    </body>
    </html>
    """
    context = {
        "title": f"{APP_TITLE} · {title}",
        "active_tab": active_tab,
        "body": body_html,
        "logged_in": require_login(),
    }
    context.update(extra)
    return render_template_string(base_template, **context)


# ---------- Auth ----------


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == ADMIN_USER and password == ADMIN_PASS:
            session["logged_in"] = True
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
        error = "Invalid credentials"
    body = """
    <form method="post">
      <div class="form-grid">
        <div>
          <label>Username</label>
          <input type="text" name="username" placeholder="admin" required>
        </div>
        <div>
          <label>Password</label>
          <input type="password" name="password" placeholder="••••••••" required>
        </div>
      </div>
      <div style="margin-top:14px;display:flex;justify-content:flex-end;">
        <button class="btn primary" type="submit">Sign in</button>
      </div>
      {% if error %}
        <div class="message">{{ error }}</div>
      {% endif %}
    </form>
    """
    return render_page("Login", "login", body, {"error": error})


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- Dashboard ----------


@app.route("/")
def dashboard():
    db = load_db()
    proxies = load_proxies()
    total = len(proxies)
    assigned = len(db.get("assigned", {}))
    available = max(total - assigned, 0)
    clients_count = len(db.get("clients", []))
    downloads = sum(c.get("download_count", 0) for c in db.get("clients", []))

    body = render_template_string(
        """
        <div class="grid">
          <div class="card">
            <h3>Proxies</h3>
            <div class="card-main">
              <span class="value">{{ total }}</span>
              <span class="label">TOTAL IN POOL</span>
            </div>
            <div style="display:flex;gap:8px;font-size:12px;margin-top:6px;">
              <span class="pill">
                <span class="pill-dot green"></span>
                Available: <strong>{{ available }}</strong>
              </span>
              <span class="pill">
                <span class="pill-dot orange"></span>
                Assigned: <strong>{{ assigned }}</strong>
              </span>
            </div>
          </div>

          <div class="card">
            <h3>Clients</h3>
            <div class="card-main">
              <span class="value">{{ clients_count }}</span>
              <span class="label">ACTIVE CLIENTS</span>
            </div>
            <div style="font-size:12px;margin-top:6px;">
              <span class="pill">
                <span class="pill-dot blue"></span>
                Total downloads: <strong>{{ downloads }}</strong>
              </span>
            </div>
          </div>

          <div class="card">
            <h3>Pool status</h3>
            <div style="font-size:12px;line-height:1.6;margin-top:4px;">
              <div>Master proxy file:<br><span style="color:#e5e7eb;">{{ proxy_file }}</span></div>
              <div style="margin-top:6px;">Database file:<br><span style="color:#e5e7eb;">{{ db_file }}</span></div>
            </div>
          </div>
        </div>

        <div class="section" style="margin-top:18px;">
          <div class="section-header">
            <div>
              <div class="section-title">Recent activity</div>
              <div class="section-sub">Last 10 events</div>
            </div>
          </div>
          {% if not history %}
            <div class="section-sub">No events yet. Create a client to start assigning proxies.</div>
          {% else %}
            <table>
              <thead>
                <tr><th style="width:160px;">When</th><th>Event</th><th>Details</th></tr>
              </thead>
              <tbody>
                {% for item in history %}
                  <tr>
                    <td>{{ item.timestamp }}</td>
                    <td><span class="badge blue">{{ item.event }}</span></td>
                    <td>{{ item.details }}</td>
                  </tr>
                {% endfor %}
              </tbody>
            </table>
          {% endif %}
        </div>
        """,
        total=total,
        available=available,
        assigned=assigned,
        clients_count=clients_count,
        downloads=downloads,
        proxy_file=PROXY_FILE,
        db_file=DB_FILE,
        history=list(reversed(db.get("history", [])))[0:10],
    )
    return render_page("Overview", "overview", body)


# ---------- Data helpers ----------


def get_client_by_id(db, client_id):
    for c in db.get("clients", []):
        if c.get("id") == client_id:
            return c
    return None


def get_available_proxies(db, master_list, wanted):
    assigned = set(db.get("assigned", {}).keys())
    available = [p for p in master_list if p not in assigned]
    return available[:wanted]


# ---------- Proxies tab ----------


@app.route("/proxies")
def proxies_view():
    db = load_db()
    proxies = load_proxies()
    assigned_map = db.get("assigned", {})
    clients_by_id = {c["id"]: c for c in db.get("clients", [])}

    page_size = 100
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1
    if page < 1:
        page = 1
    total = len(proxies)
    pages = max(math.ceil(total / page_size), 1)
    if page > pages:
        page = pages
    start = (page - 1) * page_size
    subset = proxies[start : start + page_size]

    rows = []
    for proxy in subset:
        client_id = assigned_map.get(proxy)
        client_name = clients_by_id.get(client_id, {}).get("name") if client_id else None
        rows.append({"proxy": proxy, "client": client_name})

    body = render_template_string(
        """
        <div class="section">
          <div class="section-header">
            <div>
              <div class="section-title">Proxy list</div>
              <div class="section-sub">Showing {{ start+1 }}–{{ end }} of {{ total }} proxies</div>
            </div>
          </div>
          <table>
            <thead>
              <tr><th style="width:55%;">Proxy</th><th>Assigned to</th></tr>
            </thead>
            <tbody>
              {% for row in rows %}
                <tr>
                  <td>{{ row.proxy }}</td>
                  <td>
                    {% if row.client %}
                      <span class="badge green">Client: {{ row.client }}</span>
                    {% else %}
                      <span class="badge">Available</span>
                    {% endif %}
                  </td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
          <div class="pagination">
            {% if page > 1 %}
              <a href="{{ url_for('proxies_view', page=page-1) }}">&laquo; Prev</a>
            {% endif %}
            <span class="current">Page {{ page }} / {{ pages }}</span>
            {% if page < pages %}
              <a href="{{ url_for('proxies_view', page=page+1) }}">Next &raquo;</a>
            {% endif %}
          </div>
        </div>
        """,
        rows=rows,
        total=total,
        start=start,
        end=min(start + len(subset), total),
        page=page,
        pages=pages,
    )
    return render_page("Proxies", "proxies", body)


# ---------- Clients tab ----------


@app.route("/clients")
def clients_view():
    db = load_db()
    clients = db.get("clients", [])
    body = render_template_string(
        """
        <div class="section">
          <div class="section-header">
            <div>
              <div class="section-title">Clients</div>
              <div class="section-sub">Each client gets its own proxy file on download.</div>
            </div>
            <div>
              <a href="{{ url_for('new_client') }}" class="btn primary">+ New client</a>
            </div>
          </div>
          {% if not clients %}
            <div class="section-sub">No clients yet. Create your first one.</div>
          {% else %}
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th style="width:110px;">Proxies</th>
                  <th style="width:160px;">Created</th>
                  <th style="width:110px;">Downloads</th>
                  <th style="width:210px;">Actions</th>
                </tr>
              </thead>
              <tbody>
                {% for c in clients %}
                  <tr>
                    <td>{{ c.name }}</td>
                    <td>{{ c.proxies|length }}</td>
                    <td>{{ c.created_at }}</td>
                    <td>{{ c.download_count|default(0) }}</td>
                    <td>
                      <a class="btn small outline" href="{{ url_for('download_client', client_id=c.id) }}">Download list</a>
                      <a class="btn small" href="{{ url_for('edit_client', client_id=c.id) }}">Edit</a>
                      <form class="inline" method="post" action="{{ url_for('delete_client', client_id=c.id) }}" onsubmit="return confirm('Delete client {{ c.name }} and release its proxies?');">
                        <button class="btn small" type="submit">Delete</button>
                      </form>
                    </td>
                  </tr>
                {% endfor %}
              </tbody>
            </table>
          {% endif %}
        </div>
        """,
        clients=clients,
    )
    return render_page("Clients", "clients", body)


@app.route("/clients/new", methods=["GET", "POST"])
def new_client():
    db = load_db()
    proxies = load_proxies()
    total = len(proxies)
    assigned = len(db.get("assigned", {}))
    available = max(total - assigned, 0)

    error = None
    success = None

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        try:
            count = int(request.form.get("count", "0"))
        except ValueError:
            count = 0
        password = request.form.get("password", "").strip()

        if not name:
            error = "Client name is required."
        elif count <= 0:
            error = "Number of proxies must be greater than zero."
        elif count > available:
            error = f"Only {available} proxies are available."
        else:
            to_assign = get_available_proxies(db, proxies, count)
            if len(to_assign) < count:
                error = f"Only {len(to_assign)} proxies could be assigned."
            else:
                if not password:
                    password = os.urandom(5).hex()
                next_id = max([c.get("id", 0) for c in db.get("clients", [])] or [0]) + 1
                client = {
                    "id": next_id,
                    "name": name,
                    "password": password,
                    "proxies": to_assign,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "download_count": 0,
                }
                db["clients"].append(client)
                for p in to_assign:
                    db["assigned"][p] = next_id
                db["history"].append(
                    {
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "event": "CREATE_CLIENT",
                        "details": f"{name} assigned {count} proxies.",
                    }
                )
                save_db(db)
                success = f"Client {name} created with {count} proxies. Client password: {password}"
    body = render_template_string(
        """
        <div class="section">
          <div class="section-header">
            <div>
              <div class="section-title">New client</div>
              <div class="section-sub">Assign proxies and generate a downloadable list.</div>
            </div>
            <div class="section-sub">Available proxies: <strong>{{ available }}</strong></div>
          </div>
          <form method="post">
            <div class="form-grid">
              <div>
                <label>Client name</label>
                <input type="text" name="name" placeholder="Client name" required>
              </div>
              <div>
                <label>Number of proxies</label>
                <input type="number" name="count" min="1" max="{{ available }}" required>
              </div>
              <div>
                <label>Client password (optional)</label>
                <input type="text" name="password" placeholder="Leave blank for auto-generate">
              </div>
            </div>
            <div style="margin-top:14px;display:flex;justify-content:flex-end;gap:8px;">
              <a href="{{ url_for('clients_view') }}" class="btn">Cancel</a>
              <button type="submit" class="btn primary">Create client</button>
            </div>
            {% if error %}
              <div class="message">{{ error }}</div>
            {% endif %}
            {% if success %}
              <div class="message success">{{ success }}</div>
            {% endif %}
          </form>
        </div>
        """,
        available=available,
        error=error,
        success=success,
    )
    return render_page("New client", "clients", body)


@app.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
def edit_client(client_id):
    db = load_db()
    client = get_client_by_id(db, client_id)
    if not client:
        return redirect(url_for("clients_view"))

    error = None
    success = None

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "").strip()
        if not name:
            error = "Client name is required."
        else:
            client["name"] = name
            if password:
                client["password"] = password
            db["history"].append(
                {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "event": "UPDATE_CLIENT",
                    "details": f"{client['name']} updated.",
                }
            )
            save_db(db)
            success = "Client updated."

    body = render_template_string(
        """
        <div class="section">
          <div class="section-header">
            <div>
              <div class="section-title">Edit client</div>
              <div class="section-sub">Update name or password. Proxies stay the same.</div>
            </div>
          </div>
          <form method="post">
            <div class="form-grid">
              <div>
                <label>Client name</label>
                <input type="text" name="name" value="{{ client.name }}" required>
              </div>
              <div>
                <label>Client password</label>
                <input type="text" name="password" value="{{ client.password }}">
              </div>
              <div>
                <label>Proxies assigned (read-only)</label>
                <input type="text" value="{{ client.proxies|length }} proxies" disabled>
              </div>
            </div>
            <div style="margin-top:14px;display:flex;justify-content:flex-end;gap:8px;">
              <a href="{{ url_for('clients_view') }}" class="btn">Back</a>
              <button type="submit" class="btn primary">Save changes</button>
            </div>
            {% if error %}
              <div class="message">{{ error }}</div>
            {% endif %}
            {% if success %}
              <div class="message success">{{ success }}</div>
            {% endif %}
          </form>
        </div>
        """,
        client=client,
        error=error,
        success=success,
    )
    return render_page("Edit client", "clients", body)


@app.post("/clients/<int:client_id>/delete")
def delete_client(client_id):
    db = load_db()
    client = get_client_by_id(db, client_id)
    if not client:
        return redirect(url_for("clients_view"))
    name = client["name"]
    proxies = set(client.get("proxies", []))
    db["clients"] = [c for c in db.get("clients", []) if c.get("id") != client_id]
    assigned = db.get("assigned", {})
    for p in list(assigned.keys()):
        if assigned[p] == client_id or p in proxies:
            assigned.pop(p, None)
    db["history"].append(
        {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event": "DELETE_CLIENT",
            "details": f"{name} deleted and proxies released.",
        }
    )
    save_db(db)
    return redirect(url_for("clients_view"))


@app.route("/clients/<int:client_id>/download")
def download_client(client_id):
    db = load_db()
    client = get_client_by_id(db, client_id)
    if not client:
        return redirect(url_for("clients_view"))
    proxies = client.get("proxies", [])
    if not proxies:
        content = "# No proxies assigned\n"
    else:
        content = "\n".join(proxies) + "\n"
    client["download_count"] = client.get("download_count", 0) + 1
    save_db(db)
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in client["name"])
    filename = f"{len(proxies)}_proxies_{safe_name or 'client'}.txt"
    return Response(
        content,
        mimetype="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------- History ----------


@app.route("/history")
def history_view():
    db = load_db()
    history = list(reversed(db.get("history", [])))
    body = render_template_string(
        """
        <div class="section">
          <div class="section-header">
            <div>
              <div class="section-title">History</div>
              <div class="section-sub">All panel events (latest first).</div>
            </div>
          </div>
          {% if not history %}
            <div class="section-sub">No events recorded yet.</div>
          {% else %}
            <table>
              <thead>
                <tr><th style="width:160px;">When</th><th>Event</th><th>Details</th></tr>
              </thead>
              <tbody>
                {% for item in history %}
                  <tr>
                    <td>{{ item.timestamp }}</td>
                    <td><span class="badge blue">{{ item.event }}</span></td>
                    <td>{{ item.details }}</td>
                  </tr>
                {% endfor %}
              </tbody>
            </table>
          {% endif %}
        </div>
        """,
        history=history,
    )
    return render_page("History", "history", body)


# ---------- Compatibility route ----------


@app.route("/proxies.txt")
def proxies_txt():
    # Old URL -> redirect to dashboard, but still protected by login
    if not require_login():
        return redirect(url_for("login", next=url_for("proxies_txt")))
    return redirect(url_for("dashboard"))


# ---------- Main ----------


def main():
    parser = argparse.ArgumentParser(description="Proxy panel")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1991)
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
