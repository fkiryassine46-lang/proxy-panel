#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Modern proxy management panel with SaaS-style dashboard UI.

- Login: admin / lolopolo
- Reads proxies from /root/proxies.txt
  Each line: IP:PORT[:USER:PASS]
- Manages clients and assigns proxies.
- "Check ALL Proxies" performs HTTPS request to Google via each proxy.
  Shows live progress bar + OK / FAIL counts.
"""

import argparse
import datetime as _dt
import json
import os
import threading
import time
from typing import List, Dict, Any

import requests
from flask import (
    Flask,
    render_template_string,
    request,
    redirect,
    url_for,
    session,
    jsonify,
    Response,
    flash,
)

# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

PROXY_SOURCE_FILE = "/root/proxies.txt"
CLIENT_DB_FILE = "/root/proxy_panel_db.json"

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "lolopolo"  # local only, ne pas exposer sur Internet

CHECK_TIMEOUT = 20  # secondes pour tester un proxy
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

app = Flask(__name__)
app.secret_key = "proxy-panel-secret-change-me"

# ---------------------------------------------------------------------
# Données en mémoire
# ---------------------------------------------------------------------

PROXIES: List[Dict[str, Any]] = []
CLIENTS: List[Dict[str, Any]] = []

CHECK_STATE = {
    "running": False,
    "total": 0,
    "done": 0,
    "ok": 0,
    "fail": 0,
    "started_at": None,
    "finished_at": None,
}

STATE_LOCK = threading.Lock()

# ---------------------------------------------------------------------
# Chargement / sauvegarde
# ---------------------------------------------------------------------


def _load_clients() -> None:
    """Charge la base clients depuis le fichier JSON."""
    global CLIENTS
    if not os.path.exists(CLIENT_DB_FILE):
        CLIENTS = []
        return
    try:
        with open(CLIENT_DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        CLIENTS = data.get("clients", [])
    except Exception:
        CLIENTS = []


def _save_clients() -> None:
    data = {"clients": CLIENTS}
    tmp = CLIENT_DB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CLIENT_DB_FILE)


def _load_proxies() -> None:
    """Lit /root/proxies.txt et reconstruit la liste des proxys."""
    global PROXIES
    PROXIES = []

    if not os.path.exists(PROXY_SOURCE_FILE):
        return

    with open(PROXY_SOURCE_FILE, "r", encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    for idx, line in enumerate(lines, start=1):
        PROXIES.append(
            {
                "id": idx,
                "proxy": line,
                "assigned_to": "",
                "status": "unchecked",  # unchecked / ok / fail / checking
            }
        )

    # réapplique les assignations depuis les clients
    for client in CLIENTS:
        for p in client.get("proxies", []):
            for proxy in PROXIES:
                if proxy["proxy"] == p:
                    proxy["assigned_to"] = client["name"]


def _init_data() -> None:
    _load_clients()
    _load_proxies()


# ---------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------


def login_required(fn):
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)

    wrapper.__name__ = fn.__name__
    return wrapper


# ---------------------------------------------------------------------
# Vérification des proxys
# ---------------------------------------------------------------------


def _proxy_to_requests_dict(proxy_str: str) -> Dict[str, str]:
    """
    proxy_str : ip:port ou ip:port:user:pass
    """
    parts = proxy_str.split(":")
    if len(parts) == 2:
        host, port = parts
        auth = ""
    else:
        host, port, user, password = parts[0], parts[1], parts[2], parts[3]
        auth = f"{user}:{password}@"
    url = f"http://{auth}{host}:{port}"
    return {"http": url, "https": url}


def _run_check_all():
    """Thread de vérification 'Check ALL Proxies'."""
    global PROXIES

    with STATE_LOCK:
        CHECK_STATE["running"] = True
        CHECK_STATE["total"] = len(PROXIES)
        CHECK_STATE["done"] = 0
        CHECK_STATE["ok"] = 0
        CHECK_STATE["fail"] = 0
        CHECK_STATE["started_at"] = time.time()
        CHECK_STATE["finished_at"] = None

    # marque tout en checking pour l'UI
    for p in PROXIES:
        p["status"] = "checking"

    for proxy in PROXIES:
        proxy_str = proxy["proxy"]
        ok = False
        try:
            proxies = _proxy_to_requests_dict(proxy_str)
            resp = requests.get(
                "https://www.google.com",
                proxies=proxies,
                timeout=CHECK_TIMEOUT,
                headers={"User-Agent": USER_AGENT},
            )
            ok = resp.status_code == 200
        except Exception:
            ok = False

        proxy["status"] = "ok" if ok else "fail"

        with STATE_LOCK:
            CHECK_STATE["done"] += 1
            if ok:
                CHECK_STATE["ok"] += 1
            else:
                CHECK_STATE["fail"] += 1

    with STATE_LOCK:
        CHECK_STATE["running"] = False
        CHECK_STATE["finished_at"] = time.time()


# ---------------------------------------------------------------------
# Templates HTML
# ---------------------------------------------------------------------

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Proxy Panel - Login</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      --bg1: #8a4fff;
      --bg2: #ff6fd8;
    }
    * { box-sizing: border-box; margin:0; padding:0;
        font-family: system-ui, -apple-system, BlinkMacSystemFont,
                     "SF Pro Text","Segoe UI",sans-serif; }
    body {
      min-height:100vh;
      background:
        radial-gradient(circle at top left, var(--bg1), #1b1333),
        radial-gradient(circle at bottom right, var(--bg2), #050816);
      display:flex;
      align-items:center;
      justify-content:center;
      padding:24px;
    }
    .card {
      width:360px;
      background:rgba(15,23,42,0.94);
      border-radius:24px;
      padding:22px 20px 20px;
      color:#f9fafb;
      box-shadow:0 30px 80px rgba(15,23,42,0.9);
    }
    .logo-row { display:flex; align-items:center; gap:10px; margin-bottom:14px; }
    .logo-badge {
      width:36px;height:36px;border-radius:999px;
      background:conic-gradient(from 140deg,#ff6fd8,#8a4fff,#4f46e5,#ff6fd8);
      display:flex;align-items:center;justify-content:center;
      font-weight:700;font-size:18px;color:white;
    }
    .logo-main { font-size:11px;text-transform:uppercase;letter-spacing:0.12em;}
    .logo-sub { font-size:12px;color:#9ca3af; }
    .title { font-size:20px;font-weight:700;margin-bottom:4px;}
    .subtitle { font-size:12px;color:#9ca3af;margin-bottom:16px;}
    .label { font-size:12px;margin-bottom:4px;}
    .input {
      width:100%;border-radius:12px;border:1px solid rgba(148,163,184,0.55);
      padding:7px 10px;background:rgba(15,23,42,0.96);
      color:#f9fafb;font-size:13px;outline:none;margin-bottom:12px;
    }
    .input:focus {
      border-color:#8b5cf6;
      box-shadow:0 0 0 1px rgba(139,92,246,0.6);
    }
    .btn {
      width:100%;border:none;border-radius:999px;
      padding:8px 14px;margin-top:4px;
      background:linear-gradient(135deg,#8a4fff,#5b21ff);
      color:white;font-size:13px;font-weight:500;
      cursor:pointer;box-shadow:0 12px 25px rgba(88,28,135,0.7);
    }
    .btn:hover { box-shadow:0 16px 40px rgba(88,28,135,0.9); }
    .flash-error {
      margin-bottom:10px;padding:8px 10px;border-radius:12px;
      background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.5);
      font-size:13px;color:#fecaca;
    }
    .footer { margin-top:10px;font-size:11px;color:#9ca3af;text-align:center;}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo-row">
      <div class="logo-badge">P</div>
      <div>
        <div class="logo-main">PROXY PANEL</div>
        <div class="logo-sub">Local admin access</div>
      </div>
    </div>

    <div class="title">Admin login</div>
    <div class="subtitle">Sign in to manage proxies and clients.</div>

    {% if error %}
      <div class="flash-error">{{ error }}</div>
    {% endif %}

    <form method="post">
      <div class="label">Username</div>
      <input class="input" type="text" name="username" value="admin" autocomplete="username">
      <div class="label">Password</div>
      <input class="input" type="password" name="password" autocomplete="current-password">
      <button class="btn" type="submit">Sign in</button>
    </form>

    <div class="footer">
      Local-only panel · Default admin: <b>admin / lolopolo</b>
    </div>
  </div>
</body>
</html>
"""

LAYOUT_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Proxy Panel - {{ page_title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">

  <style>
    :root {
      --bg-gradient-1: #8a4fff;
      --bg-gradient-2: #ff6fd8;
      --card-bg: #ffffff;
      --text-main: #1f2933;
      --text-muted: #6b7280;
      --accent: #8a4fff;
      --accent-soft: rgba(138,79,255,0.12);
      --accent-strong: #5b21ff;
      --danger: #ff4b6a;
      --success: #16c784;
      --border-subtle: rgba(148,163,184,0.35);
    }
    * {
      box-sizing:border-box;margin:0;padding:0;
      font-family:system-ui,-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif;
    }
    body {
      min-height:100vh;
      background:
        radial-gradient(circle at top left, var(--bg-gradient-1), #1b1333),
        radial-gradient(circle at bottom right, var(--bg-gradient-2), #050816);
      color:var(--text-main);
    }
    a { text-decoration:none;color:inherit; }

    .layout {
      display:grid;
      grid-template-columns:260px 1fr;
      max-width:1400px;
      margin:0 auto;
      padding:24px;
      gap:24px;
    }
    .sidebar {
      background:rgba(15,23,42,0.92);
      border-radius:28px;
      padding:24px 20px;
      color:#e5e7eb;
      box-shadow:0 28px 60px rgba(15,23,42,0.75);
    }
    .sidebar-logo {
      display:flex;align-items:center;gap:10px;margin-bottom:28px;
    }
    .sidebar-logo-badge {
      width:36px;height:36px;border-radius:999px;
      background:conic-gradient(from 140deg,#ff6fd8,#8a4fff,#4f46e5,#ff6fd8);
      display:flex;align-items:center;justify-content:center;
      color:#fff;font-weight:700;font-size:18px;
    }
    .sidebar-logo-text-main {
      font-weight:700;letter-spacing:0.12em;font-size:11px;text-transform:uppercase;
    }
    .sidebar-logo-text-sub { font-size:12px;color:#9ca3af; }

    .sidebar-nav { margin-top:16px; }
    .sidebar-section-title {
      font-size:11px;text-transform:uppercase;opacity:0.55;
      letter-spacing:0.12em;margin-bottom:8px;
    }
    .sidebar-link {
      display:flex;align-items:center;justify-content:space-between;
      padding:9px 12px;border-radius:12px;margin-bottom:6px;
      font-size:14px;color:#cbd5f5;cursor:pointer;
      transition:background 0.15s ease,color 0.15s ease,transform 0.05s ease;
    }
    .sidebar-link-active {
      background:linear-gradient(90deg,rgba(248,250,252,0.08),rgba(248,250,252,0.02));
      color:#fff;transform:translateX(2px);
    }
    .sidebar-link:hover { background:rgba(148,163,184,0.12); }
    .sidebar-pill {
      font-size:11px;padding:2px 8px;border-radius:999px;
      background:rgba(148,163,184,0.24);color:#f9fafb;
    }
    .sidebar-footer {
      margin-top:32px;padding-top:16px;
      border-top:1px solid rgba(30,64,175,0.55);
      font-size:12px;color:#9ca3af;
    }

    .main {
      background:rgba(248,250,252,0.98);
      border-radius:32px;
      padding:22px 24px 26px;
      box-shadow:0 30px 80px rgba(15,23,42,0.55);
    }
    .topbar {
      display:flex;justify-content:space-between;align-items:center;
      margin-bottom:18px;
    }
    .topbar-title { font-size:22px;font-weight:700;letter-spacing:0.02em; }
    .topbar-subtitle { font-size:13px;color:var(--text-muted); }
    .topbar-right { display:flex;align-items:center;gap:10px; }
    .badge-soft {
      padding:4px 10px;border-radius:999px;font-size:11px;
      background:var(--accent-soft);color:var(--accent-strong);
    }

    .btn {
      border-radius:999px;border:none;cursor:pointer;
      padding:8px 16px;font-size:13px;font-weight:500;
      display:inline-flex;align-items:center;justify-content:center;gap:6px;
      transition:transform 0.06s,box-shadow 0.12s,opacity 0.15s;
    }
    .btn-primary {
      background:linear-gradient(135deg,var(--accent),var(--accent-strong));
      color:#fff;box-shadow:0 12px 25px rgba(88,28,135,0.60);
    }
    .btn-primary:hover {
      transform:translateY(-1px);
      box-shadow:0 16px 40px rgba(88,28,135,0.74);
    }
    .btn-secondary { background:#e5e7eb;color:#111827; }
    .btn:disabled { opacity:0.6;cursor:default;transform:none;box-shadow:none;}

    .content-grid {
      display:grid;grid-template-columns:repeat(3,minmax(0,1fr));
      gap:14px;margin-bottom:16px;
    }
    .card {
      background:var(--card-bg);border-radius:20px;
      padding:14px 16px;border:1px solid var(--border-subtle);
    }
    .card-title { font-size:13px;color:var(--text-muted);margin-bottom:6px;}
    .card-value { font-size:22px;font-weight:700; }
    .card-footer { margin-top:4px;font-size:11px;color:var(--text-muted); }

    .pill {
      display:inline-flex;align-items:center;justify-content:center;
      padding:3px 10px;border-radius:999px;
      font-size:11px;font-weight:600;letter-spacing:0.06em;
      text-transform:uppercase;
    }
    .pill-ok { background:rgba(22,199,132,0.10);color:var(--success);}
    .pill-fail { background:rgba(255,75,106,0.10);color:var(--danger);}
    .pill-unchecked { background:rgba(148,163,184,0.16);color:#4b5563;}
    .pill-checking { background:rgba(59,130,246,0.10);color:#2563eb;}

    table { width:100%;border-collapse:collapse;font-size:13px;}
    thead { background:#f3f4f6; }
    th,td { padding:9px 10px;border-bottom:1px solid #e5e7eb;text-align:left;}
    th {
      font-size:11px;text-transform:uppercase;letter-spacing:0.10em;
      color:#9ca3af;
    }
    .muted { color:var(--text-muted);font-size:12px; }
    .text-right { text-align:right; }

    .flash {
      margin-bottom:10px;padding:9px 12px;border-radius:12px;font-size:13px;
    }
    .flash-error {
      background:rgba(255,75,106,0.06);color:var(--danger);
      border:1px solid rgba(255,75,106,0.35);
    }
    .flash-success {
      background:rgba(22,199,132,0.06);color:var(--success);
      border:1px solid rgba(22,199,132,0.35);
    }

    .progress-wrapper { margin-top:10px; }
    .progress-track {
      position:relative;width:100%;height:8px;border-radius:999px;
      background:#e5e7eb;overflow:hidden;
    }
    .progress-bar {
      position:absolute;top:0;left:0;bottom:0;width:0%;
      border-radius:999px;
      background:linear-gradient(90deg,var(--accent),var(--bg-gradient-2));
      transition:width 0.25s ease-out;
    }
    .progress-label {
      margin-top:4px;font-size:11px;color:var(--text-muted);
      display:flex;justify-content:space-between;
    }
    .hidden { display:none; }

    .field-label { font-size:12px;margin-bottom:4px; }
    .field-input {
      border-radius:10px;border:1px solid #d1d5db;padding:6px 9px;
      font-size:13px;width:100%;background:#f9fafb;color:#111827;
      outline:none;
    }
    .field-input:focus {
      border-color:#8b5cf6;
      box-shadow:0 0 0 1px rgba(139,92,246,0.35);
    }

    @media (max-width:960px) {
      .layout { grid-template-columns:1fr; }
    }
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <div class="sidebar-logo">
        <div class="sidebar-logo-badge">P</div>
        <div>
          <div class="sidebar-logo-text-main">PROXY PANEL</div>
          <div class="sidebar-logo-text-sub">ISP Residential Manager</div>
        </div>
      </div>

      <div class="sidebar-nav">
        <div class="sidebar-section-title">Overview</div>

        <a href="{{ url_for('dashboard') }}"
           class="sidebar-link {% if active == 'dashboard' %}sidebar-link-active{% endif %}">
          <span>Dashboard</span>
          {% if active == 'dashboard' %}<span class="sidebar-pill">Live</span>{% endif %}
        </a>

        <a href="{{ url_for('proxies') }}"
           class="sidebar-link {% if active == 'proxies' %}sidebar-link-active{% endif %}">
          <span>Proxies</span>
          <span class="sidebar-pill">{{ totals.total }} total</span>
        </a>

        <a href="{{ url_for('clients') }}"
           class="sidebar-link {% if active == 'clients' %}sidebar-link-active{% endif %}">
          <span>Clients</span>
          <span class="sidebar-pill">{{ totals.clients }} users</span>
        </a>

        <a href="{{ url_for('settings') }}"
           class="sidebar-link {% if active == 'settings' %}sidebar-link-active{% endif %}">
          <span>Settings</span>
        </a>
      </div>

      <div class="sidebar-footer">
        Logged in as <strong>admin</strong><br>
        <a href="{{ url_for('logout') }}" style="color:#e5e7eb;text-decoration:underline;">Logout</a>
      </div>
    </aside>

    <main class="main">
      <div class="topbar">
        <div>
          <div class="topbar-title">{{ page_title }}</div>
          <div class="topbar-subtitle">
            Local-only proxy management · {{ totals.total }} proxies · {{ totals.clients }} clients
          </div>
        </div>
        <div class="topbar-right">
          <div class="badge-soft">LAN only</div>
        </div>
      </div>

      {% with msgs = get_flashed_messages(with_categories=true) %}
        {% if msgs %}
          {% for cat, msg in msgs %}
            <div class="flash {% if cat == 'error' %}flash-error{% else %}flash-success{% endif %}">
              {{ msg }}
            </div>
          {% endfor %}
        {% endif %}
      {% endwith %}

      {{ content|safe }}
    </main>
  </div>

  {{ extra_js|safe }}
</body>
</html>
"""


def _render_page(active: str, content_html: str, page_title: str, extra_js: str = ""):
    totals = {"total": len(PROXIES), "clients": len(CLIENTS)}
    return render_template_string(
        LAYOUT_HTML,
        active=active,
        content=content_html,
        page_title=page_title,
        totals=totals,
        extra_js=extra_js,
    )


# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["logged_in"] = True
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
        else:
            error = "Invalid credentials."
    return render_template_string(LOGIN_HTML, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    total = len(PROXIES)
    assigned = sum(1 for p in PROXIES if p["assigned_to"])
    available = total - assigned
    ok_count = sum(1 for p in PROXIES if p["status"] == "ok")
    fail_count = sum(1 for p in PROXIES if p["status"] == "fail")

    content = f"""
    <div class="content-grid">
      <div class="card">
        <div class="card-title">Total proxies in pool</div>
        <div class="card-value">{total}</div>
        <div class="card-footer">
          <span style="color:#16a34a;">{available} available</span> ·
          <span style="color:#f97316;">{assigned} assigned</span>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Last health check</div>
        <div class="card-value">{ok_count} OK</div>
        <div class="card-footer">
          <span style="color:#16c784;">Healthy</span> · {fail_count} failed
        </div>
      </div>

      <div class="card">
        <div class="card-title">Clients</div>
        <div class="card-value">{len(CLIENTS)}</div>
        <div class="card-footer">
          Each client download has its own proxy file.
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:14px;">
      <div class="card-title" style="margin-bottom:4px;">Quick actions</div>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
        <a href="{url_for('proxies')}" class="btn btn-primary">Open proxy list</a>
        <a href="{url_for('clients')}" class="btn btn-secondary">Manage clients</a>
      </div>
    </div>
    """
    return _render_page("dashboard", content, "Dashboard")


@app.route("/proxies")
@login_required
def proxies():
    total = len(PROXIES)
    assigned = sum(1 for p in PROXIES if p["assigned_to"])
    available = total - assigned
    ok_count = sum(1 for p in PROXIES if p["status"] == "ok")
    fail_count = sum(1 for p in PROXIES if p["status"] == "fail")

    rows_html = []
    for p in PROXIES:
        st = p["status"]
        if st == "ok":
            pill_class = "pill-ok"
            label = "OK"
        elif st == "fail":
            pill_class = "pill-fail"
            label = "FAILED"
        elif st == "checking":
            pill_class = "pill-checking"
            label = "CHECKING…"
        else:
            pill_class = "pill-unchecked"
            label = "UNCHECKED"
        rows_html.append(
            f"<tr>"
            f"<td>{p['proxy']}</td>"
            f"<td>{p['assigned_to'] or '<span class=\"muted\">Unassigned</span>'}</td>"
            f"<td><span class='pill {pill_class}'>{label}</span></td>"
            f"</tr>"
        )

    content = f"""
    <div class="content-grid">
      <div class="card">
        <div class="card-title">Proxies</div>
        <div class="card-value">{total}</div>
        <div class="card-footer">
          <span style="color:#16c784;">{available} available</span> ·
          <span style="color:#f97316;">{assigned} assigned</span>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Last health summary</div>
        <div class="card-value">{ok_count} OK</div>
        <div class="card-footer">
          <span style="color:#16c784;">OK: {ok_count}</span> ·
          <span style="color:#ef4444;">Failed: {fail_count}</span>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Health check</div>
        <div class="card-footer">
          Performs HTTPS request to Google using each proxy (timeout {CHECK_TIMEOUT}s).
        </div>
        <div style="margin-top:8px;display:flex;gap:10px;">
          <button id="check-all-btn" class="btn btn-primary" type="button"
                  onclick="startCheckAll()">Check ALL Proxies</button>
          <button class="btn btn-secondary" type="button"
                  onclick="window.location.reload()">Refresh</button>
        </div>
        <div id="check-progress" class="progress-wrapper hidden">
          <div class="progress-track">
            <div id="progress-bar" class="progress-bar"></div>
          </div>
          <div class="progress-label">
            <span id="progress-label-text">Waiting…</span>
            <span id="progress-stats"></span>
          </div>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:14px;">
      <div class="card-title" style="margin-bottom:8px;">Proxy list</div>
      <div style="max-height:480px;overflow:auto;border-radius:14px;border:1px solid #e5e7eb;">
        <table>
          <thead>
            <tr>
              <th>Proxy</th>
              <th>Assigned to</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows_html)}
          </tbody>
        </table>
      </div>
    </div>
    """

    extra_js = f"""
    <script>
      let pollTimer = null;

      function updateProgressView(data) {{
        const wrapper = document.getElementById('check-progress');
        const bar = document.getElementById('progress-bar');
        const label = document.getElementById('progress-label-text');
        const stats = document.getElementById('progress-stats');
        const btn = document.getElementById('check-all-btn');

        if (!wrapper) return;

        if (data.running || data.done > 0) {{
          wrapper.classList.remove('hidden');
        }}

        const total = data.total || 0;
        const done  = data.done  || 0;
        const ok    = data.ok    || 0;
        const fail  = data.fail  || 0;

        let pct = 0;
        if (total > 0) pct = Math.round(done * 100 / total);
        bar.style.width = pct + '%';

        if (data.running) {{
          label.textContent = 'Checking ' + done + '/' + total + ' (' + pct + '%)';
        }} else {{
          label.textContent = 'Last check: ' + done + '/' + total + ' (' + pct + '%)';
        }}
        stats.textContent = 'OK: ' + ok + ' · Failed: ' + fail;

        btn.disabled = !!data.running;
      }}

      async function pollStatus() {{
        try {{
          const resp = await fetch('{url_for("check_status")}');
          if (!resp.ok) return;
          const data = await resp.json();
          updateProgressView(data);
          if (data.running) {{
            if (!pollTimer) pollTimer = setTimeout(pollStatus, 2000);
          }} else {{
            if (pollTimer) {{
              clearTimeout(pollTimer);
              pollTimer = null;
            }}
            setTimeout(() => window.location.reload(), 800);
          }}
        }} catch (e) {{
          console.error(e);
        }}
      }}

      async function startCheckAll() {{
        const btn = document.getElementById('check-all-btn');
        btn.disabled = true;
        try {{
          const resp = await fetch('{url_for("check_all_proxies")}', {{
            method:'POST',
            headers:{{'X-Requested-With':'XMLHttpRequest'}}
          }});
          if (!resp.ok) {{
            btn.disabled = false;
            return;
          }}
          const data = await resp.json();
          if (data.status === 'started' || data.status === 'already_running') {{
            updateProgressView(data.state);
            pollStatus();
          }} else {{
            btn.disabled = false;
          }}
        }} catch (e) {{
          console.error(e);
          btn.disabled = false;
        }}
      }}

      // On load, récupérer l'état si un check est déjà en cours
      (async function() {{
        try {{
          const resp = await fetch('{url_for("check_status")}');
          if (!resp.ok) return;
          const data = await resp.json();
          if (data.running || data.done > 0) {{
            updateProgressView(data);
            if (data.running) pollStatus();
          }}
        }} catch(e) {{}}
      }})();
    </script>
    """

    return _render_page("proxies", content, "Proxies", extra_js=extra_js)


@app.route("/proxies/check-all", methods=["POST"])
@login_required
def check_all_proxies():
    with STATE_LOCK:
        if CHECK_STATE["running"]:
            return jsonify({"status": "already_running", "state": CHECK_STATE})
        t = threading.Thread(target=_run_check_all, daemon=True)
        t.start()
        state_snapshot = dict(CHECK_STATE)
    return jsonify({"status": "started", "state": state_snapshot})


@app.route("/proxies/check-status")
@login_required
def check_status():
    with STATE_LOCK:
        return jsonify(dict(CHECK_STATE))


@app.route("/clients", methods=["GET", "POST"])
@login_required
def clients():
    global CLIENTS

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        try:
            count = int(request.form.get("count", "0"))
        except ValueError:
            count = 0

        if not name or count <= 0:
            flash("Please provide a client name and a valid proxy count.", "error")
        else:
            available = [p for p in PROXIES if not p["assigned_to"]]
            if len(available) < count:
                flash(
                    f"Not enough available proxies ({len(available)} free, requested {count}).",
                    "error",
                )
            else:
                selected = available[:count]
                for p in selected:
                    p["assigned_to"] = name

                client_id = (max((c["id"] for c in CLIENTS), default=0) + 1) if CLIENTS else 1
                client = {
                    "id": client_id,
                    "name": name,
                    "count": len(selected),
                    "created_at": _dt.datetime.utcnow().isoformat() + "Z",
                    "proxies": [p["proxy"] for p in selected],
                }
                CLIENTS.append(client)
                _save_clients()

                content = "\n".join(client["proxies"])
                filename = f"{name}_{len(selected)}proxies.txt"
                return Response(
                    content,
                    mimetype="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                )

    rows_html = []
    for c in CLIENTS:
        created = c.get("created_at", "")[:19].replace("T", " ")
        rows_html.append(
            f"""
            <tr>
              <td>{c['name']}</td>
              <td>{c['count']}</td>
              <td>{created}</td>
              <td class="text-right">
                <a class="btn btn-secondary" style="padding:4px 10px;font-size:12px;"
                   href="{url_for('download_client', client_id=c['id'])}">Download</a>
                <form method="post" action="{url_for('delete_client', client_id=c['id'])}"
                      style="display:inline-block;margin-left:4px;"
                      onsubmit="return confirm('Delete this client and release proxies?');">
                  <button class="btn" style="padding:4px 10px;font-size:12px;background:#fee2e2;color:#b91c1c;">
                    Delete
                  </button>
                </form>
              </td>
            </tr>
            """
        )

    available_count = sum(1 for p in PROXIES if not p["assigned_to"])

    content = f"""
    <div class="content-grid">
      <div class="card">
        <div class="card-title">Total clients</div>
        <div class="card-value">{len(CLIENTS)}</div>
        <div class="card-footer">Each client gets a dedicated proxy export file.</div>
      </div>
      <div class="card">
        <div class="card-title">Available proxies</div>
        <div class="card-value">{available_count}</div>
        <div class="card-footer">Only free proxies can be assigned to new clients.</div>
      </div>
      <div class="card">
        <div class="card-title">Create client & export</div>
        <form method="post" style="margin-top:4px;">
          <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;">
            <div>
              <div class="field-label">Client name</div>
              <input name="name" class="field-input">
            </div>
            <div>
              <div class="field-label">Number of proxies</div>
              <input name="count" type="number" min="1" class="field-input" style="width:120px;">
            </div>
            <div>
              <button class="btn btn-primary" type="submit" style="margin-top:18px;">Create & download</button>
            </div>
          </div>
        </form>
      </div>
    </div>

    <div class="card" style="margin-top:14px;">
      <div class="card-title" style="margin-bottom:8px;">Existing clients</div>
      <div style="max-height:420px;overflow:auto;border-radius:14px;border:1px solid #e5e7eb;">
        <table>
          <thead>
            <tr>
              <th>Client</th>
              <th># Proxies</th>
              <th>Created at (UTC)</th>
              <th class="text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows_html) if rows_html else '<tr><td colspan="4" class="muted">No clients yet.</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>
    """
    return _render_page("clients", content, "Clients")


@app.route("/clients/<int:client_id>/download")
@login_required
def download_client(client_id: int):
    client = next((c for c in CLIENTS if c["id"] == client_id), None)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("clients"))

    content = "\n".join(client["proxies"])
    filename = f"{client['name']}_{client['count']}proxies.txt"
    return Response(
        content,
        mimetype="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/clients/<int:client_id>/delete", methods=["POST"])
@login_required
def delete_client(client_id: int):
    global CLIENTS
    client = next((c for c in CLIENTS if c["id"] == client_id), None)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("clients"))

    to_free = set(client["proxies"])
    for p in PROXIES:
        if p["proxy"] in to_free:
            p["assigned_to"] = ""

    CLIENTS = [c for c in CLIENTS if c["id"] != client_id]
    _save_clients()
    flash("Client deleted and proxies released.", "success")
    return redirect(url_for("clients"))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    global ADMIN_PASSWORD
    if request.method == "POST":
        new_pass = request.form.get("new_password", "").strip()
        if new_pass:
            ADMIN_PASSWORD = new_pass
            flash("Admin password updated (only for this run).", "success")
        else:
            flash("Please provide a new password.", "error")

    content = """
    <div class="card">
      <div class="card-title">Admin password</div>
      <div class="card-footer" style="margin-bottom:10px;">
        Change the local admin password (in-memory only, resets if the script restarts).
      </div>
      <form method="post" style="max-width:420px;">
        <div class="field-label">New password</div>
        <input type="password" name="new_password" class="field-input" style="margin-bottom:10px;">
        <button class="btn btn-primary" type="submit">Update password</button>
      </form>
    </div>
    """
    return _render_page("settings", content, "Settings")


# ---------------------------------------------------------------------
# Entrée principale
# ---------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Proxy panel")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=1991, type=int)
    args = parser.parse_args()

    _init_data()
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
