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
LOGIN_TEMPLATE = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>{{ title }} · Connexion</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root{
      --bg0:#070914;
      --bg1:#0a1024;
      --card: rgba(20, 24, 40, .62);
      --card2: rgba(20, 24, 40, .42);
      --stroke: rgba(255,255,255,.08);
      --stroke2: rgba(255,255,255,.12);
      --text: rgba(255,255,255,.92);
      --muted: rgba(255,255,255,.62);
      --shadow: 0 28px 80px rgba(0,0,0,.55);
      --shadow2: 0 10px 30px rgba(0,0,0,.35);
      --blue:#3b82f6;
      --pink:#ff2d6f;
      --green:#22c55e;
      --amber:#fbbf24;
      --radius: 22px;
      --radius2: 16px;
      --ring: rgba(59,130,246,.35);
      --bgimg: url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A//www.w3.org/2000/svg%27%20width%3D%271600%27%20height%3D%27900%27%20viewBox%3D%270%200%201600%20900%27%3E%0A%3Cdefs%3E%0A%20%20%3ClinearGradient%20id%3D%27g%27%20x1%3D%270%27%20y1%3D%270%27%20x2%3D%271%27%20y2%3D%271%27%3E%0A%20%20%20%20%3Cstop%20offset%3D%270%27%20stop-color%3D%27%230b0f1a%27/%3E%0A%20%20%20%20%3Cstop%20offset%3D%271%27%20stop-color%3D%27%230a1024%27/%3E%0A%20%20%3C/linearGradient%3E%0A%20%20%3Cfilter%20id%3D%27n%27%3E%0A%20%20%20%20%3CfeTurbulence%20type%3D%27fractalNoise%27%20baseFrequency%3D%27.8%27%20numOctaves%3D%273%27%20stitchTiles%3D%27stitch%27/%3E%0A%20%20%20%20%3CfeColorMatrix%20type%3D%27saturate%27%20values%3D%27.1%27/%3E%0A%20%20%20%20%3CfeComponentTransfer%3E%0A%20%20%20%20%20%20%3CfeFuncA%20type%3D%27table%27%20tableValues%3D%270%200.18%27/%3E%0A%20%20%20%20%3C/feComponentTransfer%3E%0A%20%20%3C/filter%3E%0A%3C/defs%3E%0A%3Crect%20width%3D%271600%27%20height%3D%27900%27%20fill%3D%27url%28%23g%29%27/%3E%0A%3Cg%20opacity%3D%27.55%27%3E%0A%20%20%3Cg%20fill%3D%27%23111a33%27%3E%0A%20%20%20%20%3Crect%20x%3D%27120%27%20y%3D%27150%27%20width%3D%27260%27%20height%3D%27600%27%20rx%3D%2722%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27420%27%20y%3D%27120%27%20width%3D%27280%27%20height%3D%27660%27%20rx%3D%2722%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27740%27%20y%3D%27160%27%20width%3D%27260%27%20height%3D%27590%27%20rx%3D%2722%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271040%27%20y%3D%27130%27%20width%3D%27300%27%20height%3D%27640%27%20rx%3D%2722%27/%3E%0A%20%20%3C/g%3E%0A%20%20%3Cg%20fill%3D%27%231b2750%27%20opacity%3D%27.75%27%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27190%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27248%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27306%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27364%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27422%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27480%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27538%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27596%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27654%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%3C/g%3E%0A%20%20%3Cg%20fill%3D%27%231b2750%27%20opacity%3D%27.7%27%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27170%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27228%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27286%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27344%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27402%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27460%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27518%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27576%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27634%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27692%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%3C/g%3E%0A%20%20%3Cg%20fill%3D%27%231b2750%27%20opacity%3D%27.65%27%3E%0A%20%20%20%20%3Crect%20x%3D%27770%27%20y%3D%27200%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27770%27%20y%3D%27258%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27770%27%20y%3D%27316%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27770%27%20y%3D%27374%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27770%27%20y%3D%27432%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27770%27%20y%3D%27490%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27770%27%20y%3D%27548%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27770%27%20y%3D%27606%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%3C/g%3E%0A%20%20%3Cg%20fill%3D%27%231b2750%27%20opacity%3D%27.62%27%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27180%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27238%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27296%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27354%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27412%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27470%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27528%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27586%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27644%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%3C/g%3E%0A%20%20%3Cg%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27214%27%20r%3D%275%27%20fill%3D%27%2334d399%27/%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27272%27%20r%3D%275%27%20fill%3D%27%2360a5fa%27/%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27330%27%20r%3D%275%27%20fill%3D%27%23f472b6%27/%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27388%27%20r%3D%275%27%20fill%3D%27%23fbbf24%27/%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27446%27%20r%3D%275%27%20fill%3D%27%2334d399%27/%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27504%27%20r%3D%275%27%20fill%3D%27%2360a5fa%27/%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27562%27%20r%3D%275%27%20fill%3D%27%23f472b6%27/%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27620%27%20r%3D%275%27%20fill%3D%27%23fbbf24%27/%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27678%27%20r%3D%275%27%20fill%3D%27%2334d399%27/%3E%0A%20%20%3C/g%3E%0A%3C/g%3E%0A%3Crect%20width%3D%271600%27%20height%3D%27900%27%20filter%3D%27url%28%23n%29%27%20opacity%3D%27.35%27/%3E%0A%3C/svg%3E");
      --glass: blur(16px);
    }
    *{box-sizing:border-box}
    html,body{height:100%}
    body{
      margin:0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji","Segoe UI Emoji";
      color:var(--text);
      background:
        radial-gradient(1200px 600px at 15% 20%, rgba(59,130,246,.22), transparent 55%),
        radial-gradient(900px 500px at 70% 10%, rgba(255,45,111,.18), transparent 55%),
        radial-gradient(1000px 700px at 60% 80%, rgba(34,197,94,.10), transparent 60%),
        linear-gradient(180deg, var(--bg0), var(--bg1));
      position:relative;
      overflow:hidden;
    }
    body::before{
      content:"";
      position:fixed; inset:0;
      background-image: var(--bgimg);
      background-size: cover;
      background-position: center;
      opacity:.35;
      transform: scale(1.03);
      filter: saturate(.95) contrast(1.05);
      pointer-events:none;
    }
    body::after{
      content:"";
      position:fixed; inset:-2px;
      background: linear-gradient(180deg, rgba(7,9,20,.84), rgba(10,16,36,.88));
      pointer-events:none;
    }
    .wrap{
      position:relative;
      min-height:100%;
      display:grid;
      place-items:center;
      padding: 28px 18px;
    }
    .card{
      width:min(980px, 100%);
      display:grid;
      grid-template-columns: 1.05fr .95fr;
      gap: 18px;
      padding: 18px;
      border-radius: calc(var(--radius) + 6px);
      background: rgba(15, 18, 32, .46);
      border: 1px solid var(--stroke);
      box-shadow: var(--shadow);
      backdrop-filter: var(--glass);
      -webkit-backdrop-filter: var(--glass);
    }
    .hero{
      border-radius: var(--radius);
      padding: 28px;
      background:
        radial-gradient(800px 420px at 20% 20%, rgba(59,130,246,.35), transparent 60%),
        radial-gradient(700px 420px at 70% 10%, rgba(255,45,111,.28), transparent 55%),
        radial-gradient(700px 420px at 30% 90%, rgba(34,197,94,.12), transparent 60%),
        rgba(255,255,255,.03);
      border: 1px solid rgba(255,255,255,.08);
      box-shadow: var(--shadow2);
      overflow:hidden;
      position:relative;
      min-height: 320px;
    }
    .brand{
      display:flex; align-items:center; gap:12px;
      font-weight:700;
      letter-spacing:.2px;
      margin-bottom: 16px;
      opacity:.98;
    }
    .logo{
      width:42px; height:42px; border-radius:14px;
      background: linear-gradient(135deg, rgba(59,130,246,.9), rgba(255,45,111,.9));
      box-shadow: 0 18px 44px rgba(0,0,0,.35);
      position:relative;
      overflow:hidden;
    }
    .logo::after{
      content:"";
      position:absolute; inset:-30%;
      background: radial-gradient(circle at 30% 30%, rgba(255,255,255,.55), transparent 55%);
      transform: rotate(20deg);
      opacity:.55;
    }
    .hero h1{margin: 10px 0 8px; font-size: 28px; line-height:1.15}
    .hero p{margin:0; color:var(--muted); font-size: 14.5px; line-height:1.55; max-width: 46ch}
    .chips{margin-top: 18px; display:flex; flex-wrap:wrap; gap:10px}
    .chip{
      display:inline-flex; align-items:center; gap:8px;
      padding: 10px 12px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,.10);
      background: rgba(255,255,255,.04);
      color: rgba(255,255,255,.78);
      font-size: 13px;
    }
    .dot{width:10px; height:10px; border-radius:999px; background: var(--green); box-shadow: 0 0 0 4px rgba(34,197,94,.14)}
    .dot.blue{background: var(--blue); box-shadow: 0 0 0 4px rgba(59,130,246,.14)}
    .dot.pink{background: var(--pink); box-shadow: 0 0 0 4px rgba(255,45,111,.14)}
    .form{
      border-radius: var(--radius);
      padding: 26px;
      background: rgba(255,255,255,.03);
      border: 1px solid rgba(255,255,255,.08);
      box-shadow: var(--shadow2);
      position:relative;
    }
    .form h2{margin:0 0 8px; font-size: 18px}
    .form .sub{margin:0 0 16px; color:var(--muted); font-size: 13.5px}
    .field{margin: 12px 0}
    label{display:block; font-size: 12.5px; color: rgba(255,255,255,.72); margin: 0 0 7px 2px}
    input{
      width:100%;
      padding: 12px 14px;
      border-radius: 14px;
      background: rgba(10, 12, 22, .55);
      border: 1px solid rgba(255,255,255,.10);
      color: var(--text);
      outline:none;
      transition: border-color .2s, box-shadow .2s, transform .05s;
    }
    input:focus{
      border-color: rgba(59,130,246,.45);
      box-shadow: 0 0 0 6px rgba(59,130,246,.14);
    }
    .btn{
      width:100%;
      margin-top: 10px;
      padding: 12px 14px;
      border:0;
      border-radius: 999px;
      cursor:pointer;
      color: white;
      font-weight:700;
      letter-spacing:.2px;
      background: linear-gradient(90deg, rgba(34,197,94,.95), rgba(34,197,94,.82));
      box-shadow: 0 20px 50px rgba(34,197,94,.18);
      transition: transform .08s ease, filter .15s ease;
    }
    .btn:hover{filter: brightness(1.03)}
    .btn:active{transform: translateY(1px)}
    .error{
      margin-top: 12px;
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid rgba(255,45,111,.28);
      background: rgba(255,45,111,.12);
      color: rgba(255,255,255,.88);
      font-size: 13px;
    }
    .foot{margin-top: 14px; text-align:center; color: rgba(255,255,255,.45); font-size: 12.5px}
    @media (max-width: 860px){
      .card{grid-template-columns: 1fr;}
      .hero{min-height: 260px}
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="hero">
        <div class="brand">
          <div class="logo" aria-hidden="true"></div>
          <div>{{ title }}</div>
        </div>
        <h1>Dashboard Proxy Panel</h1>
        <p>Interface moderne, rapide et lisible. Connexion sécurisée, gestion centralisée et monitoring des proxies.</p>
        <div class="chips">
          <div class="chip"><span class="dot"></span> Sessions</div>
          <div class="chip"><span class="dot blue"></span> Monitoring</div>
          <div class="chip"><span class="dot pink"></span> Exports</div>
        </div>
      </div>

      <div class="form">
        <h2>Connexion</h2>
        <p class="sub">Entre tes identifiants pour accéder au panel.</p>

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

        <div class="foot">© {{ title }} · Secure Panel</div>
      </div>
    </div>
  </div>
</body>
</html>
"""

# ---- LAYOUT : thème dashboard corporate ----
LAYOUT_TEMPLATE = """{% macro nav_link(href, label, active_name) -%}
  <a href="{{ href }}" class="nav-item {{ 'is-active' if active == active_name else '' }}">{{ label }}</a>
{%- endmacro %}
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>{{ title }} · {{ page_title }}</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root{
      --bg0:#070914;
      --bg1:#0a1024;
      --panel: rgba(20, 24, 40, .60);
      --panel2: rgba(20, 24, 40, .42);
      --stroke: rgba(255,255,255,.08);
      --stroke2: rgba(255,255,255,.12);
      --text: rgba(255,255,255,.92);
      --muted: rgba(255,255,255,.62);
      --shadow: 0 28px 80px rgba(0,0,0,.55);
      --shadow2: 0 10px 30px rgba(0,0,0,.35);
      --blue:#3b82f6;
      --pink:#ff2d6f;
      --green:#22c55e;
      --amber:#fbbf24;
      --radius: 22px;
      --radius2: 16px;
      --glass: blur(16px);
      --bgimg: url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A//www.w3.org/2000/svg%27%20width%3D%271600%27%20height%3D%27900%27%20viewBox%3D%270%200%201600%20900%27%3E%0A%3Cdefs%3E%0A%20%20%3ClinearGradient%20id%3D%27g%27%20x1%3D%270%27%20y1%3D%270%27%20x2%3D%271%27%20y2%3D%271%27%3E%0A%20%20%20%20%3Cstop%20offset%3D%270%27%20stop-color%3D%27%230b0f1a%27/%3E%0A%20%20%20%20%3Cstop%20offset%3D%271%27%20stop-color%3D%27%230a1024%27/%3E%0A%20%20%3C/linearGradient%3E%0A%20%20%3Cfilter%20id%3D%27n%27%3E%0A%20%20%20%20%3CfeTurbulence%20type%3D%27fractalNoise%27%20baseFrequency%3D%27.8%27%20numOctaves%3D%273%27%20stitchTiles%3D%27stitch%27/%3E%0A%20%20%20%20%3CfeColorMatrix%20type%3D%27saturate%27%20values%3D%27.1%27/%3E%0A%20%20%20%20%3CfeComponentTransfer%3E%0A%20%20%20%20%20%20%3CfeFuncA%20type%3D%27table%27%20tableValues%3D%270%200.18%27/%3E%0A%20%20%20%20%3C/feComponentTransfer%3E%0A%20%20%3C/filter%3E%0A%3C/defs%3E%0A%3Crect%20width%3D%271600%27%20height%3D%27900%27%20fill%3D%27url%28%23g%29%27/%3E%0A%3Cg%20opacity%3D%27.55%27%3E%0A%20%20%3Cg%20fill%3D%27%23111a33%27%3E%0A%20%20%20%20%3Crect%20x%3D%27120%27%20y%3D%27150%27%20width%3D%27260%27%20height%3D%27600%27%20rx%3D%2722%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27420%27%20y%3D%27120%27%20width%3D%27280%27%20height%3D%27660%27%20rx%3D%2722%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27740%27%20y%3D%27160%27%20width%3D%27260%27%20height%3D%27590%27%20rx%3D%2722%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271040%27%20y%3D%27130%27%20width%3D%27300%27%20height%3D%27640%27%20rx%3D%2722%27/%3E%0A%20%20%3C/g%3E%0A%20%20%3Cg%20fill%3D%27%231b2750%27%20opacity%3D%27.75%27%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27190%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27248%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27306%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27364%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27422%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27480%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27538%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27596%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27150%27%20y%3D%27654%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%3C/g%3E%0A%20%20%3Cg%20fill%3D%27%231b2750%27%20opacity%3D%27.7%27%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27170%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27228%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27286%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27344%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27402%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27460%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27518%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27576%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27634%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27460%27%20y%3D%27692%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%3C/g%3E%0A%20%20%3Cg%20fill%3D%27%231b2750%27%20opacity%3D%27.65%27%3E%0A%20%20%20%20%3Crect%20x%3D%27770%27%20y%3D%27200%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27770%27%20y%3D%27258%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27770%27%20y%3D%27316%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27770%27%20y%3D%27374%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27770%27%20y%3D%27432%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27770%27%20y%3D%27490%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27770%27%20y%3D%27548%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%27770%27%20y%3D%27606%27%20width%3D%27200%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%3C/g%3E%0A%20%20%3Cg%20fill%3D%27%231b2750%27%20opacity%3D%27.62%27%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27180%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27238%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27296%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27354%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27412%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27470%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27528%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27586%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%20%20%3Crect%20x%3D%271080%27%20y%3D%27644%27%20width%3D%27220%27%20height%3D%2734%27%20rx%3D%2710%27/%3E%0A%20%20%3C/g%3E%0A%20%20%3Cg%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27214%27%20r%3D%275%27%20fill%3D%27%2334d399%27/%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27272%27%20r%3D%275%27%20fill%3D%27%2360a5fa%27/%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27330%27%20r%3D%275%27%20fill%3D%27%23f472b6%27/%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27388%27%20r%3D%275%27%20fill%3D%27%23fbbf24%27/%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27446%27%20r%3D%275%27%20fill%3D%27%2334d399%27/%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27504%27%20r%3D%275%27%20fill%3D%27%2360a5fa%27/%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27562%27%20r%3D%275%27%20fill%3D%27%23f472b6%27/%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27620%27%20r%3D%275%27%20fill%3D%27%23fbbf24%27/%3E%0A%20%20%20%20%3Ccircle%20cx%3D%27310%27%20cy%3D%27678%27%20r%3D%275%27%20fill%3D%27%2334d399%27/%3E%0A%20%20%3C/g%3E%0A%3C/g%3E%0A%3Crect%20width%3D%271600%27%20height%3D%27900%27%20filter%3D%27url%28%23n%29%27%20opacity%3D%27.35%27/%3E%0A%3C/svg%3E");
    }
    *{box-sizing:border-box}
    html,body{height:100%}
    body{
      margin:0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji","Segoe UI Emoji";
      color:var(--text);
      background:
        radial-gradient(1200px 600px at 15% 20%, rgba(59,130,246,.22), transparent 55%),
        radial-gradient(900px 500px at 70% 10%, rgba(255,45,111,.18), transparent 55%),
        radial-gradient(1000px 700px at 60% 80%, rgba(34,197,94,.10), transparent 60%),
        linear-gradient(180deg, var(--bg0), var(--bg1));
      min-height:100%;
      position:relative;
      overflow-x:hidden;
    }
    body::before{
      content:"";
      position:fixed; inset:0;
      background-image: var(--bgimg);
      background-size: cover;
      background-position: center;
      opacity:.22;
      transform: scale(1.03);
      filter: saturate(.95) contrast(1.05);
      pointer-events:none;
    }
    body::after{
      content:"";
      position:fixed; inset:-2px;
      background: linear-gradient(180deg, rgba(7,9,20,.82), rgba(10,16,36,.90));
      pointer-events:none;
    }
    a{color:inherit; text-decoration:none}
    .container{position:relative; width:min(1280px, 100%); margin: 0 auto; padding: 18px 16px 40px;}
    /* Top Bar */
    .topbar{
      position:sticky; top:0; z-index:50;
      padding: 14px 16px;
      margin: 12px 0 18px;
      border-radius: calc(var(--radius) + 6px);
      background: rgba(15, 18, 32, .46);
      border: 1px solid var(--stroke);
      box-shadow: var(--shadow2);
      backdrop-filter: var(--glass);
      -webkit-backdrop-filter: var(--glass);
      display:flex; align-items:center; gap: 14px;
    }
    .brand{display:flex; align-items:center; gap:12px; min-width: 190px}
    .logo{width:42px; height:42px; border-radius:14px;
      background: linear-gradient(135deg, rgba(59,130,246,.9), rgba(255,45,111,.9));
      box-shadow: 0 18px 44px rgba(0,0,0,.35);
      position:relative; overflow:hidden;
    }
    .logo::after{content:""; position:absolute; inset:-30%;
      background: radial-gradient(circle at 30% 30%, rgba(255,255,255,.55), transparent 55%);
      transform: rotate(20deg); opacity:.55;
    }
    .brandname{font-weight:800; letter-spacing:.2px}
    .navpill{
      display:flex; align-items:center; gap: 6px;
      padding: 6px;
      border-radius: 999px;
      background: rgba(255,255,255,.03);
      border: 1px solid rgba(255,255,255,.08);
      margin-left: 8px;
      flex: 1 1 auto;
      max-width: 520px;
    }
    .nav-item{
      padding: 10px 14px;
      border-radius: 999px;
      color: rgba(255,255,255,.68);
      font-weight: 650;
      font-size: 13.5px;
      transition: background .15s, color .15s;
      white-space:nowrap;
    }
    .nav-item:hover{background: rgba(255,255,255,.05); color: rgba(255,255,255,.86)}
    .nav-item.is-active{
      background: rgba(255,255,255,.08);
      color: rgba(255,255,255,.92);
      border: 1px solid rgba(255,255,255,.10);
    }
    .right{display:flex; align-items:center; gap:10px; margin-left:auto}
    .chip{
      display:inline-flex; align-items:center; gap:8px;
      padding: 10px 12px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,.10);
      background: rgba(255,255,255,.04);
      color: rgba(255,255,255,.78);
      font-size: 13px;
    }
    .dot{width:10px; height:10px; border-radius:999px; background: var(--green); box-shadow: 0 0 0 4px rgba(34,197,94,.14)}
    /* Cards & tables */
    .grid{display:grid; gap: 16px}
    .card{
      background: var(--panel);
      border: 1px solid var(--stroke);
      border-radius: var(--radius);
      box-shadow: var(--shadow2);
      backdrop-filter: var(--glass);
      -webkit-backdrop-filter: var(--glass);
      overflow:hidden;
    }
    .card .card-hd{
      padding: 14px 16px;
      display:flex; align-items:center; justify-content:space-between;
      border-bottom: 1px solid rgba(255,255,255,.06);
    }
    .card .card-hd h3{margin:0; font-size: 13px; letter-spacing:.2px; color: rgba(255,255,255,.78); font-weight:800; text-transform: none}
    .card .card-bd{padding: 16px}
    .btn{
      display:inline-flex; align-items:center; justify-content:center;
      padding: 10px 14px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,.10);
      background: rgba(255,255,255,.04);
      color: rgba(255,255,255,.90);
      font-weight: 750;
      cursor:pointer;
      transition: transform .08s ease, filter .15s ease, background .15s ease;
      user-select:none;
    }
    .btn:hover{background: rgba(255,255,255,.06)}
    .btn:active{transform: translateY(1px)}
    .btn.primary{
      border:0;
      background: linear-gradient(90deg, rgba(34,197,94,.95), rgba(34,197,94,.82));
      box-shadow: 0 20px 50px rgba(34,197,94,.18);
    }
    .btn.primary:hover{filter: brightness(1.03)}
    .pill{
      display:inline-flex; align-items:center; gap:8px;
      padding: 8px 10px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,.10);
      background: rgba(255,255,255,.04);
      font-size: 12.5px;
      color: rgba(255,255,255,.72);
    }
    .pill .dot{width:9px; height:9px; box-shadow:none}
    .pill.ok .dot{background: var(--green)}
    .pill.warn .dot{background: var(--amber)}
    .pill.bad .dot{background: var(--pink)}
    /* Form elements */
    input, select, textarea{
      width:100%;
      padding: 11px 12px;
      border-radius: 14px;
      background: rgba(10, 12, 22, .55);
      border: 1px solid rgba(255,255,255,.10);
      color: var(--text);
      outline:none;
    }
    input:focus, select:focus, textarea:focus{
      border-color: rgba(59,130,246,.45);
      box-shadow: 0 0 0 6px rgba(59,130,246,.14);
    }
    table{width:100%; border-collapse:separate; border-spacing:0 10px}
    thead th{text-align:left; font-size: 12px; color: rgba(255,255,255,.55); font-weight:800; padding: 0 10px 2px}
    tbody tr{background: rgba(255,255,255,.03); border: 1px solid rgba(255,255,255,.06)}
    tbody td{padding: 12px 10px; font-size: 13px; color: rgba(255,255,255,.78)}
    tbody tr td:first-child{border-top-left-radius: 14px; border-bottom-left-radius: 14px}
    tbody tr td:last-child{border-top-right-radius: 14px; border-bottom-right-radius: 14px}
    /* Utility */
    .muted{color: var(--muted)}
    .divider{height:1px; background: rgba(255,255,255,.06); margin: 14px 0}
    @media (max-width: 860px){
      .topbar{flex-wrap:wrap}
      .brand{min-width:auto}
      .navpill{order: 3; width:100%; max-width:none; margin-left:0}
      .right{width:100%; justify-content:flex-end}
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="topbar">
      <div class="brand">
        <div class="logo" aria-hidden="true"></div>
        <div class="brandname">{{ title }}</div>
      </div>

      <div class="navpill" role="navigation" aria-label="Navigation">
        {{ nav_link(url_for('dashboard'), 'Dashboard', 'dashboard') }}
        {{ nav_link(url_for('proxy_plan'), 'Proxy Plan', 'proxy_plan') }}
        {{ nav_link(url_for('auth_ip'), 'Authenticate IP', 'auth_ip') }}
      </div>

      <div class="right">
        <div class="chip"><span class="dot"></span> Online</div>
        <a class="btn" href="{{ url_for('logout') }}">Logout</a>
      </div>
    </div>

    {{ body|safe }}
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
