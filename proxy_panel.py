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
  <title>{{ title }} · Login</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root{
      --bg0:#070A12;
      --bg1:#0B1220;
      --card:rgba(17,24,39,.78);
      --stroke:rgba(148,163,184,.28);
      --text:#E5E7EB;
      --muted:#9CA3AF;
      --accent:#3B82F6;
      --accent2:#22D3EE;
      --danger:#FB923C;
      --ring:rgba(59,130,246,.35);
      --shadow: 0 28px 80px rgba(0,0,0,.55);
      --radius: 18px;
    }
    *{box-sizing:border-box}
    html,body{height:100%}
    body{
      margin:0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, "Apple Color Emoji","Segoe UI Emoji";
      color:var(--text);
      background:
        radial-gradient(900px 480px at 10% -10%, rgba(59,130,246,.28), transparent 60%),
        radial-gradient(720px 420px at 90% 0%, rgba(34,211,238,.18), transparent 55%),
        radial-gradient(520px 520px at 50% 110%, rgba(99,102,241,.10), transparent 60%),
        linear-gradient(180deg, var(--bg0), var(--bg1));
      display:flex;
      align-items:center;
      justify-content:center;
      padding:24px;
    }
    .wrap{
      width:min(420px, 100%);
    }
    .card{
      background:var(--card);
      border:1px solid var(--stroke);
      border-radius:var(--radius);
      padding:26px 26px 22px;
      box-shadow:var(--shadow);
      backdrop-filter: blur(10px);
    }
    .top{
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:12px;
      margin-bottom:14px;
    }
    .brand{
      display:flex;
      flex-direction:column;
      gap:2px;
    }
    .badge{
      font-size:11px;
      letter-spacing:.18em;
      text-transform:uppercase;
      color:var(--muted);
    }
    .logo{
      font-weight:800;
      letter-spacing:.10em;
      text-transform:uppercase;
      font-size:12px;
    }
    .logo span{color:#93C5FD}
    h1{
      margin:0 0 6px;
      font-size:22px;
      line-height:1.1;
    }
    .sub{
      margin:0 0 18px;
      color:var(--muted);
      font-size:13px;
      line-height:1.45;
    }
    label{
      display:block;
      font-size:12px;
      color:var(--muted);
      margin:0 0 6px;
    }
    .field{margin-bottom:14px}
    input{
      width:100%;
      padding:10px 11px;
      border-radius:12px;
      border:1px solid rgba(51,65,85,.9);
      background: rgba(2,6,23,.75);
      color:var(--text);
      font-size:14px;
      outline:none;
      transition: box-shadow .12s ease, border-color .12s ease, transform .12s ease;
    }
    input:focus{
      border-color: rgba(59,130,246,.95);
      box-shadow: 0 0 0 4px var(--ring);
      transform: translateY(-1px);
    }
    .btn{
      width:100%;
      border:none;
      border-radius:999px;
      padding:11px 14px;
      font-size:13px;
      font-weight:700;
      letter-spacing:.06em;
      text-transform:uppercase;
      color:white;
      cursor:pointer;
      background: linear-gradient(135deg, var(--accent), var(--accent2));
      box-shadow: 0 16px 48px rgba(59,130,246,.40);
    }
    .btn:hover{filter:brightness(1.05)}
    .hint{
      margin-top:12px;
      color:rgba(156,163,175,.85);
      font-size:11px;
      line-height:1.45;
    }
    .error{
      margin-top:12px;
      padding:10px 12px;
      border-radius:12px;
      border:1px solid rgba(251,146,60,.55);
      background: rgba(251,146,60,.12);
      color: #FED7AA;
      font-size:12px;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="top">
        <div class="brand">
          <div class="badge">Admin console</div>
          <div class="logo"><span>{{ title }}</span></div>
        </div>
      </div>

      <h1>Connexion</h1>
      <p class="sub">Accès sécurisé au panneau de gestion des proxys.</p>

      <form method="post" autocomplete="on">
        <div class="field">
          <label>Nom d’utilisateur</label>
          <input type="text" name="username" value="{{ default_user }}" autocomplete="username" required>
        </div>
        <div class="field">
          <label>Mot de passe</label>
          <input type="password" name="password" autocomplete="current-password" required>
        </div>
        <button class="btn" type="submit">Se connecter</button>
      </form>

      {% if error %}
        <div class="error">{{ error }}</div>
      {% endif %}

      <div class="hint">Astuce: change le <b>secret_key</b> Flask et le mot de passe admin pour la prod.</div>
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
  <title>{{ title }} · {{ page_title }}</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">

  <style>
    :root{
      --bg0:#070A12;
      --bg1:#0B1220;
      --panel: rgba(17,24,39,.72);
      --panel2: rgba(2,6,23,.65);
      --stroke: rgba(148,163,184,.22);
      --stroke2: rgba(148,163,184,.32);
      --text: #E5E7EB;
      --muted:#9CA3AF;
      --accent:#3B82F6;
      --accent2:#22D3EE;
      --ok:#22C55E;
      --fail:#FB923C;
      --warn:#F59E0B;
      --shadow: 0 18px 55px rgba(0,0,0,.55);
      --radius: 18px;
      --ring: rgba(59,130,246,.30);
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
    }

    /* Light theme */
    [data-theme="light"]{
      --bg0:#F7FAFF;
      --bg1:#EEF4FF;
      --panel: rgba(255,255,255,.86);
      --panel2: rgba(255,255,255,.72);
      --stroke: rgba(15,23,42,.10);
      --stroke2: rgba(15,23,42,.14);
      --text:#0F172A;
      --muted:#475569;
      --shadow: 0 18px 55px rgba(2,6,23,.10);
      --ring: rgba(59,130,246,.22);
    }

    *{box-sizing:border-box}
    html,body{height:100%}
    body{
      margin:0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, "Apple Color Emoji","Segoe UI Emoji";
      color:var(--text);
      background:
        radial-gradient(900px 520px at 10% -10%, rgba(59,130,246,.25), transparent 60%),
        radial-gradient(760px 460px at 92% 0%, rgba(34,211,238,.16), transparent 55%),
        radial-gradient(520px 520px at 50% 120%, rgba(99,102,241,.10), transparent 60%),
        linear-gradient(180deg, var(--bg0), var(--bg1));
    }

    .shell{
      max-width: 1320px;
      margin:0 auto;
      padding: 18px 18px 30px;
    }

    /* Top bar */
    header{
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:14px;
      margin-bottom:16px;
      padding: 12px 14px;
      border-radius: var(--radius);
      background: var(--panel);
      border: 1px solid var(--stroke);
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }

    .brand{
      display:flex;
      flex-direction:column;
      gap:2px;
      min-width: 190px;
    }
    .brand-title{
      font-weight:900;
      letter-spacing:.14em;
      font-size:12px;
      text-transform:uppercase;
    }
    .brand-title span{color:#93C5FD}
    [data-theme="light"] .brand-title span{color:#2563EB}
    .brand-sub{
      font-size:11px;
      color:var(--muted);
    }

    nav{
      display:flex;
      gap:10px;
      align-items:center;
      flex-wrap:wrap;
      justify-content:flex-end;
    }

    .nav-link{
      font-size:13px;
      padding:8px 14px;
      border-radius:999px;
      color:var(--muted);
      text-decoration:none;
      border:1px solid var(--stroke2);
      background: var(--panel2);
      transition: transform .12s ease, border-color .12s ease, color .12s ease, filter .12s ease;
      user-select:none;
    }
    .nav-link:hover{
      border-color: rgba(59,130,246,.75);
      color: var(--text);
      transform: translateY(-1px);
    }
    .nav-link.active{
      color:white;
      border-color: transparent;
      background: linear-gradient(135deg, var(--accent), var(--accent2));
      box-shadow: 0 14px 40px rgba(59,130,246,.35);
    }

    .icon-btn, .logout-link{
      font-size:12px;
      padding:8px 12px;
      border-radius:999px;
      border:1px solid var(--stroke2);
      background: var(--panel2);
      color: var(--muted);
      text-decoration:none;
      cursor:pointer;
      transition: transform .12s ease, border-color .12s ease, color .12s ease;
      user-select:none;
    }
    .icon-btn:hover, .logout-link:hover{
      border-color: rgba(34,211,238,.65);
      color: var(--text);
      transform: translateY(-1px);
    }

    h2{
      margin: 14px 2px 14px;
      font-size:20px;
      letter-spacing:.01em;
    }

    .grid{
      display:grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 14px;
      margin-bottom: 16px;
    }

    .card{
      background: var(--panel);
      border: 1px solid var(--stroke);
      border-radius: var(--radius);
      padding: 16px 18px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }
    .card h3{
      margin:0 0 8px;
      font-size:13px;
      letter-spacing:.02em;
      color: var(--text);
    }
    .big{
      font-size:30px;
      font-weight:800;
      letter-spacing:-.02em;
    }
    .muted{color:var(--muted); font-size:12px}
    code{
      font-family: var(--mono);
      font-size: 12px;
      padding: 2px 6px;
      border-radius: 10px;
      border: 1px solid var(--stroke2);
      background: rgba(2,6,23,.35);
    }
    [data-theme="light"] code{ background: rgba(15,23,42,.04); }

    /* Tables */
    table{
      width:100%;
      border-collapse: separate;
      border-spacing: 0;
      font-size:13px;
      overflow:hidden;
      border-radius: 14px;
    }
    thead th{
      position: sticky;
      top: 0;
      z-index: 1;
      background: rgba(2,6,23,.55);
      backdrop-filter: blur(10px);
    }
    [data-theme="light"] thead th{ background: rgba(255,255,255,.80); }
    th,td{
      padding: 9px 10px;
      text-align:left;
      border-bottom: 1px solid rgba(148,163,184,.14);
    }
    th{
      font-size:11px;
      text-transform:uppercase;
      letter-spacing:.10em;
      color: var(--muted);
    }
    tbody tr:hover td{
      background: rgba(59,130,246,.06);
    }

    .pill{
      display:inline-block;
      padding:3px 9px;
      border-radius:999px;
      font-size:11px;
      border:1px solid var(--stroke2);
      color: var(--muted);
      background: rgba(2,6,23,.25);
    }
    [data-theme="light"] .pill{ background: rgba(15,23,42,.03); }

    /* Buttons */
    .btn{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      gap:8px;
      padding: 8px 15px;
      border-radius: 999px;
      border:none;
      cursor:pointer;
      font-size:13px;
      font-weight:700;
      color:white;
      background: linear-gradient(135deg, var(--accent), var(--accent2));
      box-shadow: 0 14px 40px rgba(59,130,246,.30);
      transition: transform .12s ease, filter .12s ease;
    }
    .btn:hover{ filter: brightness(1.05); transform: translateY(-1px); }

    .btn-secondary{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      padding: 7px 13px;
      border-radius: 999px;
      border: 1px solid var(--stroke2);
      background: var(--panel2);
      color: var(--muted);
      box-shadow: none;
      cursor:pointer;
      text-decoration:none;
      transition: transform .12s ease, border-color .12s ease, color .12s ease;
    }
    .btn-secondary:hover{
      border-color: rgba(59,130,246,.55);
      color: var(--text);
      transform: translateY(-1px);
    }

    /* Status badges */
    .status-badge{
      padding: 3px 10px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 650;
      letter-spacing: .02em;
      display:inline-flex;
      align-items:center;
      gap:6px;
    }
    .status-badge::before{
      content:"";
      width:7px;height:7px;border-radius:99px;
      background: rgba(148,163,184,.9);
      box-shadow: 0 0 0 2px rgba(148,163,184,.18);
    }
    .status-ok{ background: rgba(34,197,94,.14); color: #BBF7D0; border: 1px solid rgba(34,197,94,.45); }
    .status-ok::before{ background: var(--ok); box-shadow: 0 0 0 2px rgba(34,197,94,.18); }
    .status-fail{ background: rgba(251,146,60,.14); color: #FED7AA; border: 1px solid rgba(251,146,60,.45); }
    .status-fail::before{ background: var(--fail); box-shadow: 0 0 0 2px rgba(251,146,60,.18); }
    .status-unknown{ background: rgba(148,163,184,.10); color: var(--text); border: 1px solid rgba(148,163,184,.30); }
    .status-checking{ background: rgba(245,158,11,.14); color: #FDE68A; border: 1px solid rgba(245,158,11,.45); }
    .status-checking::before{ background: var(--warn); box-shadow: 0 0 0 2px rgba(245,158,11,.18); }

    /* Forms */
    .form-row{
      display:flex;
      flex-wrap:wrap;
      gap: 12px;
      margin-bottom: 12px;
    }
    .form-row label{
      font-size: 12px;
      color: var(--muted);
      display:block;
      margin-bottom: 6px;
    }
    .form-row input{
      border-radius: 12px;
      border: 1px solid rgba(51,65,85,.55);
      background: rgba(2,6,23,.55);
      color: var(--text);
      padding: 9px 10px;
      min-width: 170px;
      font-size: 13px;
      outline:none;
      transition: box-shadow .12s ease, border-color .12s ease, transform .12s ease;
    }
    [data-theme="light"] .form-row input{ background: rgba(255,255,255,.75); border-color: rgba(15,23,42,.14); }
    .form-row input:focus{
      border-color: rgba(59,130,246,.85);
      box-shadow: 0 0 0 4px var(--ring);
      transform: translateY(-1px);
    }

    .error-msg{
      color: #FED7AA;
      background: rgba(251,146,60,.12);
      border: 1px solid rgba(251,146,60,.45);
      padding: 8px 10px;
      border-radius: 12px;
      font-size: 12px;
      margin-top: 6px;
      display:inline-block;
    }

    .footer-note{
      margin-top: 16px;
      font-size: 11px;
      color: var(--muted);
      text-align:center;
      opacity: .92;
    }

    /* Progress */
    .progress-wrapper{ margin-top: 10px; font-size: 12px; color: var(--muted); }
    .progress-bar-outer{
      width:100%;
      height: 8px;
      border-radius: 999px;
      background: rgba(2,6,23,.35);
      overflow:hidden;
      margin-top: 7px;
      border: 1px solid rgba(148,163,184,.14);
    }
    [data-theme="light"] .progress-bar-outer{ background: rgba(15,23,42,.04); }
    .progress-bar-inner{
      height:100%;
      width:0%;
      border-radius: 999px;
      background: linear-gradient(90deg, var(--accent), var(--accent2));
      transition: width .18s ease-out;
    }

    @media (max-width: 760px){
      header{flex-direction:column; align-items:flex-start;}
      nav{justify-content:flex-start}
      .shell{padding: 14px 14px 26px;}
    }
  </style>
</head>

<body>
  <script>
    (function(){
      const saved = localStorage.getItem('pp_theme');
      if (saved === 'light' || saved === 'dark') {
        document.documentElement.setAttribute('data-theme', saved);
      }
    })();
  </script>

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
        <button class="icon-btn" id="theme-toggle" type="button" title="Toggle theme">Theme</button>
        <a class="logout-link" href="{{ url_for('logout') }}">Logout</a>
      </nav>
    </header>

    {{ body|safe }}

    <div class="footer-note">
      Local-only admin panel · Proxy source: {{ stats.proxy_source }} · DB: {{ stats.db_file }}
    </div>
  </div>

  <script>
    (function(){
      const btn = document.getElementById('theme-toggle');
      if (!btn) return;
      function current(){ return document.documentElement.getAttribute('data-theme') || 'dark'; }
      function set(t){
        document.documentElement.setAttribute('data-theme', t);
        localStorage.setItem('pp_theme', t);
      }
      btn.addEventListener('click', () => set(current()==='dark' ? 'light' : 'dark'));
    })();
  </script>
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
