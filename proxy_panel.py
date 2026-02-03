#!/usr/bin/env python3
import os
import json
import argparse
import subprocess
from datetime import datetime

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    session,
    send_file,
    make_response,
    render_template_string,
)

# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------

PROXY_SOURCE_FILE = "/root/proxies.txt"
DB_FILE = "/root/proxy_panel_db.json"

ADMIN_USER = "admin"  # username fixé
DEFAULT_ADMIN_PASSWORD = "lolopolo"

app = Flask(__name__)
app.secret_key = "proxy-panel-secret-key"  # tu peux changer si tu veux
app.jinja_env.autoescape = True

# -------------------------------------------------------------------
# Helpers DB / proxies
# -------------------------------------------------------------------


def load_db():
    if not os.path.exists(DB_FILE):
        db = {
            "admin_password": DEFAULT_ADMIN_PASSWORD,
            "clients": [],
            "assigned": {},  # proxy_line -> client_id
            "history": [],
        }
        save_db(db)
        return db
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            db = json.load(f)
    except Exception:
        db = {
            "admin_password": DEFAULT_ADMIN_PASSWORD,
            "clients": [],
            "assigned": {},
            "history": [],
        }
    # ensure keys
    db.setdefault("admin_password", DEFAULT_ADMIN_PASSWORD)
    db.setdefault("clients", [])
    db.setdefault("assigned", {})
    db.setdefault("history", [])
    return db


def save_db(db):
    tmp = DB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2)
    os.replace(tmp, DB_FILE)


def load_proxies():
    if not os.path.exists(PROXY_SOURCE_FILE):
        return []
    lines = []
    with open(PROXY_SOURCE_FILE, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line:
                lines.append(line)
    return lines


def get_available_proxies(db, proxies, count=None):
    """Retourne la liste des proxies non assignés (optionnellement limités à count)."""
    assigned = set(db.get("assigned", {}).keys())
    free = [p for p in proxies if p not in assigned]
    if count is None:
        return free
    return free[:count]


def add_history(db, event, details):
    db["history"].append(
        {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event": event,
            "details": details,
        }
    )


def get_admin_password(db):
    return db.get("admin_password", DEFAULT_ADMIN_PASSWORD)


# -------------------------------------------------------------------
# Auth helpers
# -------------------------------------------------------------------


def is_logged_in():
    return session.get("logged_in", False)


def login_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


# -------------------------------------------------------------------
# HTML / layout
# -------------------------------------------------------------------

BASE_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ title or "Proxy Panel" }}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: radial-gradient(circle at top left, #1f2937 0, #020617 45%, #000 100%);
    color: #e5e7eb;
    min-height: 100vh;
  }
  a { color: #60a5fa; text-decoration: none; }
  a:hover { text-decoration: underline; }

  .shell {
    padding: 24px;
  }
  .navbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 24px;
  }
  .navbar-left {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .brand-dot {
    width: 10px;
    height: 10px;
    border-radius: 999px;
    background: radial-gradient(circle, #22c55e, #16a34a);
  }
  .brand-title {
    font-weight: 600;
    letter-spacing: .04em;
    font-size: 14px;
  }
  .nav-tabs {
    display: flex;
    gap: 8px;
    font-size: 14px;
  }
  .nav-tab {
    padding: 6px 14px;
    border-radius: 999px;
    border: 1px solid transparent;
    background: transparent;
    color: #9ca3af;
  }
  .nav-tab.active {
    border-color: rgba(148, 163, 184, .4);
    background: radial-gradient(circle at top left, rgba(59,130,246,.4), rgba(30,64,175,.3));
    color: #e5e7eb;
  }
  .nav-right {
    font-size: 13px;
    color: #9ca3af;
    display: flex;
    align-items: center;
    gap: 16px;
  }
  .btn-logout {
    padding: 6px 12px;
    border-radius: 999px;
    border: 1px solid rgba(148, 163, 184,.5);
    background: transparent;
    color: #e5e7eb;
    font-size: 12px;
    cursor: pointer;
  }
  .btn-logout:hover {
    background: rgba(148,163,184,.15);
  }

  .card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 18px;
    margin-bottom: 24px;
  }
  .card {
    border-radius: 18px;
    padding: 18px 20px;
    background: radial-gradient(circle at top left, rgba(15,118,110,.4), rgba(15,23,42,.9));
    border: 1px solid rgba(148,163,184,.18);
    box-shadow: 0 18px 40px rgba(0,0,0,.65);
  }
  .card-alt {
    background: radial-gradient(circle at top left, rgba(79,70,229,.4), rgba(15,23,42,.9));
  }
  .card-danger {
    background: radial-gradient(circle at top left, rgba(220,38,38,.45), rgba(15,23,42,.95));
  }
  .card-title {
    font-size: 14px;
    font-weight: 500;
    color: #9ca3af;
    margin-bottom: 10px;
  }
  .card-value {
    font-size: 32px;
    font-weight: 600;
    margin-bottom: 4px;
  }
  .card-sub {
    font-size: 13px;
    color: #9ca3af;
  }

  .content {
    border-radius: 18px;
    padding: 18px 20px 26px;
    background: radial-gradient(circle at top left, rgba(15,23,42,.95), rgba(2,6,23,.98));
    border: 1px solid rgba(31,41,55,.9);
    box-shadow: 0 20px 50px rgba(0,0,0,.9);
    min-height: 260px;
  }

  h2 {
    font-size: 18px;
    font-weight: 500;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 12px;
    font-size: 13px;
  }
  thead tr {
    background: rgba(15,23,42,.85);
  }
  th, td {
    padding: 8px 10px;
    border-bottom: 1px solid rgba(31,41,55,.8);
  }
  th {
    text-align: left;
    color: #9ca3af;
    font-weight: 500;
  }
  tbody tr:nth-child(even) {
    background: rgba(15,23,42,.6);
  }

  .pill {
    display: inline-flex;
    align-items: center;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 500;
  }
  .pill-ok {
    background: rgba(22,163,74,.25);
    color: #4ade80;
  }
  .pill-fail {
    background: rgba(220,38,38,.25);
    color: #fca5a5;
  }
  .pill-muted {
    background: rgba(148,163,184,.2);
    color: #e5e7eb;
  }

  label {
    font-size: 13px;
    display: block;
    margin-top: 12px;
    margin-bottom: 4px;
    color: #9ca3af;
  }
  input[type="text"], input[type="number"], input[type="password"] {
    width: 100%;
    padding: 8px 10px;
    border-radius: 10px;
    border: 1px solid rgba(55,65,81,.8);
    background: rgba(15,23,42,.9);
    color: #e5e7eb;
    font-size: 13px;
    outline: none;
  }
  input:focus {
    border-color: rgba(96,165,250,.9);
    box-shadow: 0 0 0 1px rgba(37,99,235,.5);
  }

  .btn-primary {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 7px 16px;
    border-radius: 999px;
    border: none;
    cursor: pointer;
    font-size: 13px;
    background: linear-gradient(90deg, #6366f1, #ec4899);
    color: white;
    box-shadow: 0 10px 22px rgba(56,189,248,.25);
  }
  .btn-primary:hover {
    filter: brightness(1.05);
  }

  .badge {
    font-size: 11px;
    padding: 2px 6px;
    border-radius: 999px;
    border: 1px solid rgba(148,163,184,.5);
    color: #9ca3af;
  }

  .muted { color: #9ca3af; font-size: 12px; }

  @media (max-width: 720px) {
    .shell { padding: 16px; }
    .navbar { flex-direction: column; align-items: flex-start; gap: 10px; }
  }
</style>
</head>
<body>
<div class="shell">
  {% if not hide_nav %}
  <div class="navbar">
    <div class="navbar-left">
      <div class="brand-dot"></div>
      <div class="brand-title">PROXY PANEL</div>
      <div class="nav-tabs">
        <a href="{{ url_for('dashboard') }}" class="nav-tab {% if current=='dashboard' %}active{% endif %}">Dashboard</a>
        <a href="{{ url_for('clients_view') }}" class="nav-tab {% if current=='clients' %}active{% endif %}">Clients</a>
        <a href="{{ url_for('proxies_view') }}" class="nav-tab {% if current=='proxies' %}active{% endif %}">Proxies</a>
        <a href="{{ url_for('settings_view') }}" class="nav-tab {% if current=='settings' %}active{% endif %}">Settings</a>
      </div>
    </div>
    <div class="nav-right">
      <span class="muted">Logged in as <strong>{{ admin_user }}</strong></span>
      <form method="post" action="{{ url_for('logout') }}">
        <button type="submit" class="btn-logout">Logout</button>
      </form>
    </div>
  </div>
  {% endif %}

  <div class="content">
    {{ content|safe }}
  </div>
</div>
</body>
</html>
"""


def render_page(title, current, body_html, extra_context=None, hide_nav=False):
    ctx = {
        "title": title,
        "current": current,
        "content": body_html,
        "hide_nav": hide_nav,
        "admin_user": ADMIN_USER,
    }
    if extra_context:
        ctx.update(extra_context)
    return render_template_string(BASE_TEMPLATE, **ctx)


# -------------------------------------------------------------------
# Login / logout
# -------------------------------------------------------------------


@app.route("/login", methods=["GET", "POST"])
def login():
    db = load_db()
    error = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == ADMIN_USER and password == get_admin_password(db):
            session["logged_in"] = True
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
        error = "Invalid credentials"

    error_html = ""
    if error:
        error_html = f"""
        <p style='margin-top:10px;font-size:13px;color:#f97373;'>
          {error}
        </p>
        """

    body = f"""
<div style="max-width:360px;margin:40px auto;">
  <div style="border-radius:18px;padding:24px 24px 22px;
              background:radial-gradient(circle at top left,rgba(59,130,246,.45),rgba(15,23,42,.98));
              border:1px solid rgba(148,163,184,.35);box-shadow:0 20px 45px rgba(0,0,0,.8);">
    <h2 style="margin-bottom:4px;">Proxy Panel</h2>
    <p class="muted" style="margin-bottom:18px;">Local admin login</p>

    <form method="post">
      <label>Username</label>
      <input type="text" name="username" value="admin" autocomplete="username">

      <label>Password</label>
      <input type="password" name="password" autocomplete="current-password">

      <div style="margin-top:16px;display:flex;justify-content:flex-end;">
        <button class="btn-primary" type="submit">Sign in</button>
      </div>
    </form>
    {error_html}
    <p class="muted" style="margin-top:16px;font-size:11px;">
      Default admin: <strong>admin</strong> / <strong>lolopolo</strong>
    </p>
  </div>
</div>
"""
    return render_page("Login", "login", body, hide_nav=True)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# -------------------------------------------------------------------
# Dashboard
# -------------------------------------------------------------------


@app.route("/")
@login_required
def dashboard():
    db = load_db()
    proxies = load_proxies()

    total = len(proxies)
    assigned_count = len(db.get("assigned", {}))
    available = max(total - assigned_count, 0)
    client_count = len(db.get("clients", []))

    recent_history = list(reversed(db.get("history", [])))[0:5]

    body = render_template_string(
        """
<div class="card-grid" style="margin-bottom:22px;">
  <div class="card card-alt">
    <div class="card-title">Proxies</div>
    <div class="card-value">{{ total }}</div>
    <div class="card-sub">
      Available: <span style="color:#4ade80;">{{ available }}</span> ·
      Assigned: <span style="color:#f97373;">{{ assigned }}</span>
    </div>
  </div>
  <div class="card">
    <div class="card-title">Clients</div>
    <div class="card-value">{{ clients }}</div>
    <div class="card-sub">Each client gets its own proxy list file.</div>
  </div>
  <div class="card card-danger">
    <div class="card-title">Pool status</div>
    <div class="card-sub">
      Master proxy file:<br>
      <code>{{ proxy_file }}</code><br><br>
      Database file:<br>
      <code>{{ db_file }}</code>
    </div>
  </div>
</div>

<h2 style="margin-bottom:12px;">Recent activity</h2>
{% if history %}
  <table>
    <thead>
      <tr><th style="width:170px;">Time</th><th>Event</th><th>Details</th></tr>
    </thead>
    <tbody>
      {% for h in history %}
      <tr>
        <td>{{ h.timestamp }}</td>
        <td><span class="badge">{{ h.event }}</span></td>
        <td>{{ h.details }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
{% else %}
  <p class="muted">No activity recorded yet.</p>
{% endif %}
""",
        total=total,
        available=available,
        assigned=assigned_count,
        clients=client_count,
        proxy_file=PROXY_SOURCE_FILE,
        db_file=DB_FILE,
        history=recent_history,
    )

    return render_page("Dashboard", "dashboard", body)


# -------------------------------------------------------------------
# Clients
# -------------------------------------------------------------------


@app.route("/clients")
@login_required
def clients_view():
    db = load_db()
    proxies = load_proxies()

    total = len(proxies)
    assigned = len(db.get("assigned", {}))
    available = max(total - assigned, 0)
    clients = db.get("clients", [])

    body = render_template_string(
        """
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">
  <h2>Clients</h2>
  <a href="{{ url_for('new_client') }}"><button class="btn-primary">New client</button></a>
</div>
<p class="muted" style="margin-bottom:8px;">
  Total proxies: {{ total }} · Available: <span style="color:#4ade80;">{{ available }}</span> · Assigned: {{ assigned }}
</p>

{% if clients %}
<table>
  <thead>
    <tr>
      <th style="width:60px;">ID</th>
      <th>Name</th>
      <th style="width:120px;">Proxies</th>
      <th style="width:160px;">Created</th>
      <th style="width:110px;">Downloads</th>
      <th style="width:180px;">Actions</th>
    </tr>
  </thead>
  <tbody>
    {% for c in clients %}
    <tr>
      <td>#{{ c.id }}</td>
      <td>{{ c.name }}</td>
      <td>{{ c.proxies|length }}</td>
      <td>{{ c.created_at }}</td>
      <td>{{ c.download_count }}</td>
      <td>
        <a href="{{ url_for('download_client', client_id=c.id) }}">Download list</a>
        &nbsp;·&nbsp;
        <form method="post" action="{{ url_for('delete_client', client_id=c.id) }}" style="display:inline;"
              onsubmit="return confirm('Delete client {{ c.name }} ?');">
          <button type="submit" style="background:none;border:none;color:#f97373;font-size:12px;cursor:pointer;">
            Delete
          </button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
  <p class="muted">No clients created yet.</p>
{% endif %}
""",
        total=total,
        available=available,
        assigned=assigned,
        clients=clients,
    )
    return render_page("Clients", "clients", body)


@app.route("/clients/new", methods=["GET", "POST"])
@login_required
def new_client():
    db = load_db()
    proxies = load_proxies()

    total = len(proxies)
    assigned = len(db.get("assigned", {}))
    available = max(total - assigned, 0)

    error = None

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        try:
            count = int(request.form.get("count", "0"))
        except ValueError:
            count = 0

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
                next_id = max([c.get("id", 0) for c in db.get("clients", [])] or [0]) + 1
                client = {
                    "id": next_id,
                    "name": name,
                    "password": os.urandom(5).hex(),  # interne, pour plus tard si besoin
                    "proxies": to_assign,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "download_count": 0,
                }
                db["clients"].append(client)
                for p in to_assign:
                    db["assigned"][p] = next_id
                add_history(
                    db,
                    "CREATE_CLIENT",
                    f"{name} assigned {count} proxies.",
                )
                save_db(db)
                # téléchargement direct
                return redirect(url_for("download_client", client_id=next_id))

    body = render_template_string(
        """
<h2 style="margin-top:0;margin-bottom:10px;">New client</h2>
<p class="muted" style="margin-bottom:8px;">
  Assign proxies and instantly download a text file for this client.
</p>
<p class="muted" style="margin-bottom:10px;">
  Available proxies: <strong>{{ available }}</strong> / {{ total }}
</p>

<form method="post" style="max-width:420px;">
  <label>Client name</label>
  <input type="text" name="name" placeholder="Client name">

  <label>Number of proxies</label>
  <input type="number" name="count" min="1" step="1" placeholder="10">

  <div style="margin-top:14px;">
    <a href="{{ url_for('clients_view') }}" class="muted" style="margin-right:14px;">Cancel</a>
    <button class="btn-primary" type="submit">Create client</button>
  </div>
</form>

{% if error %}
  <p style="margin-top:14px;font-size:13px;color:#f97373;">{{ error }}</p>
{% endif %}
""",
        total=total,
        available=available,
        error=error,
    )
    return render_page("New client", "clients", body)


@app.route("/clients/<int:client_id>/download")
@login_required
def download_client(client_id):
    db = load_db()
    clients = db.get("clients", [])
    client = next((c for c in clients if c.get("id") == client_id), None)
    if not client:
        return "Client not found", 404

    proxies = client.get("proxies", [])
    content = "\n".join(proxies) + "\n"

    client["download_count"] = int(client.get("download_count", 0)) + 1
    add_history(
        db,
        "DOWNLOAD_LIST",
        f"{client['name']} downloaded proxy list ({len(proxies)} proxies).",
    )
    save_db(db)

    safe_name = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in client["name"]
    )
    filename = f"{safe_name or 'client'}_{len(proxies)}proxies.txt"

    resp = make_response(content)
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@app.post("/clients/<int:client_id>/delete")
@login_required
def delete_client(client_id):
    db = load_db()
    clients = db.get("clients", [])
    client = next((c for c in clients if c.get("id") == client_id), None)
    if not client:
        return redirect(url_for("clients_view"))

    name = client["name"]
    proxies = client.get("proxies", [])

    clients = [c for c in clients if c.get("id") != client_id]
    db["clients"] = clients

    for p in proxies:
        if p in db.get("assigned", {}):
            db["assigned"].pop(p, None)

    add_history(db, "DELETE_CLIENT", f"{name} deleted ({len(proxies)} proxies freed).")
    save_db(db)
    return redirect(url_for("clients_view"))


# -------------------------------------------------------------------
# Proxies + check all
# -------------------------------------------------------------------


@app.route("/proxies")
@login_required
def proxies_view():
    db = load_db()
    proxies = load_proxies()
    assigned_map = db.get("assigned", {})
    clients_map = {c["id"]: c for c in db.get("clients", [])}

    page = max(int(request.args.get("page", 1)), 1)
    per_page = 100
    total = len(proxies)
    pages = max((total + per_page - 1) // per_page, 1)
    if page > pages:
        page = pages

    start = (page - 1) * per_page
    end = min(start + per_page, total)
    rows = []
    for idx, p in enumerate(proxies[start:end], start=start + 1):
        cid = assigned_map.get(p)
        client_name = clients_map.get(cid, {}).get("name") if cid else None
        rows.append(
            {
                "index": idx,
                "proxy": p,
                "assigned_to": client_name,
            }
        )

    available = len(get_available_proxies(db, proxies))
    assigned = len(assigned_map)

    body = render_template_string(
        """
<h2 style="margin-top:0;margin-bottom:10px;">Proxies</h2>
<p class="muted">
  Total: {{ total }} · Available: <span style="color:#4ade80;">{{ available }}</span> · Assigned: {{ assigned }}
</p>

{% if total == 0 %}
  <p class="muted" style="margin-top:10px;">
    No proxies found in <code>{{ proxy_file }}</code>.
  </p>
{% else %}
  <table>
    <thead>
      <tr>
        <th style="width:60px;">#</th>
        <th>Proxy</th>
        <th style="width:180px;">Status</th>
      </tr>
    </thead>
    <tbody>
      {% for r in rows %}
      <tr>
        <td>{{ r.index }}</td>
        <td>{{ r.proxy }}</td>
        <td>
          {% if r.assigned_to %}
            <span class="pill pill-muted">Assigned to {{ r.assigned_to }}</span>
          {% else %}
            <span class="pill pill-ok">Available</span>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <div style="margin-top:10px;font-size:13px;">
    {% if page > 1 %}
      <a href="{{ url_for('proxies_view', page=page-1) }}">« Prev</a>
    {% endif %}
    &nbsp; Page {{ page }} / {{ pages }} &nbsp;
    {% if page < pages %}
      <a href="{{ url_for('proxies_view', page=page+1) }}">Next »</a>
    {% endif %}
  </div>

  <form method="post" action="{{ url_for('check_all_proxies') }}" style="margin-top:16px;">
    <button class="btn-primary" type="submit">Check ALL proxies</button>
  </form>
{% endif %}
""",
        total=total,
        available=available,
        assigned=assigned,
        proxy_file=PROXY_SOURCE_FILE,
        rows=rows,
        page=page,
        pages=pages,
    )
    return render_page("Proxies", "proxies", body)


def curl_check_proxy(proxy_line, timeout=20):
    """Retourne True si proxy fonctionne (curl vers Google), False sinon."""
    proxy = proxy_line.strip()
    if not proxy:
        return False
    proxy_url = f"http://{proxy}"
    try:
        result = subprocess.run(
            [
                "curl",
                "-sS",
                "--max-time",
                str(timeout),
                "-x",
                proxy_url,
                "https://www.google.com/generate_204",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False


@app.post("/proxies/check-all")
@login_required
def check_all_proxies():
    proxies = load_proxies()
    results = []
    ok = 0

    for line in proxies:
        is_ok = curl_check_proxy(line, timeout=20)
        results.append({"proxy": line, "ok": is_ok})
        if is_ok:
            ok += 1

    total = len(proxies)
    fail = total - ok

    body = render_template_string(
        """
<h2 style="margin-top:0;margin-bottom:10px;">Proxy health check</h2>
<p class="muted" style="margin-bottom:10px;">
  Testing all proxies by reaching <strong>google.com</strong> with each proxy (timeout 20s).
</p>

<p style="margin-bottom:10px;font-size:13px;">
  Total proxies tested : <strong>{{ total }}</strong><br>
  STATUS OK : <span style="color:#4ade80;">{{ ok }}</span><br>
  Failed : <span style="color:#f97373;">{{ fail }}</span>
</p>

<table>
  <thead>
    <tr><th>Proxy</th><th style="width:130px;">Status</th></tr>
  </thead>
  <tbody>
    {% for r in results %}
    <tr>
      <td>{{ r.proxy }}</td>
      <td>
        {% if r.ok %}
          <span class="pill pill-ok">STATUS OK !</span>
        {% else %}
          <span class="pill pill-fail">STATUS FAIL</span>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
""",
        total=total,
        ok=ok,
        fail=fail,
        results=results,
    )
    return render_page("Proxies health", "proxies", body)


# -------------------------------------------------------------------
# Settings : change admin password
# -------------------------------------------------------------------


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_view():
    db = load_db()
    current_pw = get_admin_password(db)
    message = None
    error = None

    if request.method == "POST":
        new_pw = request.form.get("new_password", "").strip()
        confirm_pw = request.form.get("confirm_password", "").strip()
        if not new_pw:
            error = "New password cannot be empty."
        elif new_pw != confirm_pw:
            error = "Password confirmation does not match."
        else:
            db["admin_password"] = new_pw
            add_history(db, "CHANGE_PASSWORD", "Admin password updated.")
            save_db(db)
            current_pw = new_pw
            message = "Admin password updated successfully."

    body = render_template_string(
        """
<h2 style="margin-top:0;margin-bottom:10px;">Settings</h2>
<p class="muted" style="margin-bottom:14px;">
  Change the admin password for this panel.
</p>

<form method="post" style="max-width:420px;">
  <label>New admin password</label>
  <input type="password" name="new_password" placeholder="New password">

  <label>Confirm new password</label>
  <input type="password" name="confirm_password" placeholder="Repeat password">

  <div style="margin-top:14px;">
    <button class="btn-primary" type="submit">Update password</button>
  </div>
</form>

{% if message %}
  <p style="margin-top:14px;font-size:13px;color:#4ade80;">{{ message }}</p>
{% endif %}
{% if error %}
  <p style="margin-top:14px;font-size:13px;color:#f97373;">{{ error }}</p>
{% endif %}

<p class="muted" style="margin-top:20px;font-size:11px;">
  Default credentials (if DB is reset): <strong>admin</strong>
</p>
""",
        message=message,
        error=error,
    )

    return render_page("Settings", "settings", body)


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Simple proxy management panel.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1991)
    args = parser.parse_args()

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
