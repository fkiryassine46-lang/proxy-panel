#!/usr/bin/env python3
import http.server
import socketserver
import urllib.parse
import os
import json
import datetime
import secrets
import argparse

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "lolopolo"

PROXY_FILE = "/root/proxies.txt"
DB_FILE = "/root/proxy_panel_db.json"

SESSIONS = {}
DB = None


def load_db():
    global DB
    if DB is not None:
        return DB
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            DB = json.load(f)
    except FileNotFoundError:
        DB = {"clients": [], "assigned": []}
    except json.JSONDecodeError:
        DB = {"clients": [], "assigned": []}
    return DB


def save_db():
    if DB is None:
        return
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(DB, f, indent=2)


def read_all_proxies():
    if not os.path.exists(PROXY_FILE):
        return []
    with open(PROXY_FILE, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]
    return lines


def html_page(title, body_html):
    # Simple layout with dark theme
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    body {{
      margin: 0;
      padding: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(circle at top, #202b4b 0, #050816 45%, #02040a 100%);
      color: #f5f7ff;
    }}
    a {{ color: #7dd3fc; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .top-bar {{
      display:flex;
      justify-content: space-between;
      align-items:center;
      padding: 14px 26px;
      background: linear-gradient(90deg, rgba(15,23,42,0.95), rgba(30,64,175,0.75));
      box-shadow: 0 2px 10px rgba(0,0,0,0.5);
      position:sticky;
      top:0;
      z-index:10;
    }}
    .top-bar .logo {{
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
      font-size: 14px;
      color: #e0f2fe;
    }}
    .top-bar .user {{
      font-size: 13px;
      opacity: 0.9;
    }}
    .container {{
      max-width: 1100px;
      margin: 28px auto 40px auto;
      padding: 0 18px;
    }}
    .cards {{
      display:grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 18px;
      margin-bottom: 24px;
    }}
    .card {{
      background: linear-gradient(145deg, rgba(15,23,42,0.96), rgba(30,64,175,0.55));
      border-radius: 18px;
      padding: 18px 20px;
      box-shadow: 0 18px 30px rgba(0,0,0,0.55);
      border: 1px solid rgba(148,163,184,0.25);
      backdrop-filter: blur(18px);
    }}
    .card h2 {{
      margin: 0 0 10px 0;
      font-size: 16px;
      font-weight: 600;
    }}
    .card p.small {{
      margin: 2px 0;
      font-size: 12px;
      color: #9ca3af;
    }}
    .stat-value {{
      font-size: 24px;
      font-weight: 700;
      margin-top: 4px;
    }}
    .stat-label {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: .15em;
      color: #9ca3af;
    }}
    .badge-green {{ color:#22c55e; }}
    .badge-red {{ color:#f97373; }}
    .badge-blue {{ color:#38bdf8; }}
    .badge-amber {{ color:#facc15; }}
    .nav {{
      display:flex;
      gap: 12px;
      margin-bottom: 18px;
    }}
    .nav a {{
      font-size: 13px;
      padding: 8px 14px;
      border-radius: 999px;
      border: 1px solid rgba(148,163,184,0.35);
      background: rgba(15,23,42,0.9);
    }}
    .nav a.active {{
      background: linear-gradient(135deg,#22d3ee,#6366f1);
      color:#020617;
      border-color: transparent;
      font-weight: 600;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
      font-size: 13px;
    }}
    th, td {{
      padding: 8px 6px;
      border-bottom: 1px solid rgba(31,41,55,0.85);
    }}
    th {{
      text-align:left;
      font-size:12px;
      text-transform:uppercase;
      letter-spacing:.12em;
      color:#9ca3af;
    }}
    tr:hover td {{
      background: rgba(15,23,42,0.8);
    }}
    .btn {{
      display:inline-block;
      padding: 7px 14px;
      border-radius:999px;
      border:none;
      cursor:pointer;
      font-size:13px;
      font-weight:500;
    }}
    .btn-primary {{
      background: linear-gradient(135deg,#22d3ee,#6366f1);
      color:#020617;
    }}
    .btn-secondary {{
      background: rgba(15,23,42,0.9);
      border:1px solid rgba(148,163,184,0.5);
      color:#e5e7eb;
    }}
    .btn-primary:hover {{ filter:brightness(1.05); }}
    .btn-secondary:hover {{ background:rgba(31,41,55,0.9); }}
    form label {{
      display:block;
      font-size:13px;
      margin-bottom:4px;
      color:#e5e7eb;
    }}
    form input {{
      width:100%;
      padding:8px 10px;
      border-radius:10px;
      border:1px solid rgba(148,163,184,0.7);
      background:rgba(15,23,42,0.9);
      color:#e5e7eb;
      font-size:13px;
      margin-bottom:10px;
    }}
    .error {{
      color:#f97373;
      font-size:13px;
      margin-top:4px;
    }}
    .success {{
      color:#4ade80;
      font-size:13px;
      margin-top:4px;
    }}
    .footer {{
      text-align:center;
      font-size:11px;
      color:#6b7280;
      margin-top:26px;
    }}
  </style>
</head>
<body>
  <div class="top-bar">
    <div class="logo">Proxy Panel</div>
    <div class="user">
      <a href="/dashboard">Dashboard</a> ·
      <a href="/clients">Clients</a> ·
      <a href="/logout">Logout</a>
    </div>
  </div>
  <div class="container">
    {body_html}
    <div class="footer">
      Simple proxy management panel · local use only
    </div>
  </div>
</body>
</html>
"""


def login_page(message=""):
    msg_html = ""
    if message:
        msg_html = f'<p class="error">{message}</p>'
    body = f"""
    <div class="cards">
      <div class="card" style="max-width:420px;margin:40px auto;">
        <h2>Admin Login</h2>
        <p class="small">Use your admin credentials to access the proxy panel.</p>
        {msg_html}
        <form method="POST" action="/login">
          <label for="username">Username</label>
          <input id="username" name="username" autocomplete="off" required>
          <label for="password">Password</label>
          <input id="password" name="password" type="password" required>
          <button class="btn btn-primary" type="submit">Sign in</button>
        </form>
      </div>
    </div>
    """
    return html_page("Login", body)


def dashboard_page():
    db = load_db()
    all_proxies = read_all_proxies()
    total = len(all_proxies)
    assigned = len(db.get("assigned", []))
    available = max(total - assigned, 0)
    client_count = len(db.get("clients", []))

    body = f"""
    <div class="nav">
      <a href="/dashboard" class="active">Overview</a>
      <a href="/clients">Clients</a>
    </div>
    <div class="cards">
      <div class="card">
        <h2>Proxies</h2>
        <div class="stat-value">{total}</div>
        <div class="stat-label">Total proxies in pool</div>
        <p class="small"><span class="badge-green">Available:</span> {available} &nbsp; · &nbsp;
          <span class="badge-red">Assigned:</span> {assigned}</p>
      </div>
      <div class="card">
        <h2>Clients</h2>
        <div class="stat-value">{client_count}</div>
        <div class="stat-label">Clients created</div>
        <p class="small">Each client download gets its own text file.</p>
      </div>
      <div class="card">
        <h2>Pool status</h2>
        <p class="small">Master proxy source file:</p>
        <p class="small"><code>{PROXY_FILE}</code></p>
        <p class="small">Database file:</p>
        <p class="small"><code>{DB_FILE}</code></p>
      </div>
    </div>
    """
    return html_page("Dashboard", body)


def clients_page(message="", error=""):
    db = load_db()
    body_rows = ""
    for c in db.get("clients", []):
        body_rows += f"""
        <tr>
          <td>{c.get('created_at','')}</td>
          <td>{c.get('name','')}</td>
          <td>{len(c.get('proxies', []))}</td>
          <td><code>{c.get('filename','')}</code></td>
        </tr>
        """
    if not body_rows:
        body_rows = '<tr><td colspan="4">No clients yet.</td></tr>'

    msg_html = ""
    if message:
        msg_html = f'<p class="success">{message}</p>'
    elif error:
        msg_html = f'<p class="error">{error}</p>'

    body = f"""
    <div class="nav">
      <a href="/dashboard">Overview</a>
      <a href="/clients" class="active">Clients & allocations</a>
    </div>
    <div class="cards">
      <div class="card">
        <h2>Create a client</h2>
        <p class="small">Choose a name and how many proxies to allocate. The panel will
        pick free proxies from the pool and return a downloadable text file.</p>
        {msg_html}
        <form method="POST" action="/create_client">
          <label for="name">Client name</label>
          <input id="name" name="name" placeholder="e.g. Mohamed" required>
          <label for="count">Number of proxies</label>
          <input id="count" name="count" type="number" min="1" max="100000" value="100" required>
          <button class="btn btn-primary" type="submit">Generate file</button>
        </form>
      </div>
      <div class="card">
        <h2>History</h2>
        <p class="small">List of all generated client files.</p>
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Client</th>
              <th># Proxies</th>
              <th>File name</th>
            </tr>
          </thead>
          <tbody>
            {body_rows}
          </tbody>
        </table>
      </div>
    </div>
    """
    return html_page("Clients", body)


class ProxyPanelHandler(http.server.BaseHTTPRequestHandler):
    def get_current_user(self):
        cookie = self.headers.get("Cookie", "")
        if not cookie:
            return None
        parts = cookie.split(";")
        session_id = None
        for part in parts:
            part = part.strip()
            if part.startswith("session="):
                session_id = part[len("session="):]
                break
        if not session_id:
            return None
        return SESSIONS.get(session_id)

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def is_authenticated(self):
        return self.get_current_user() is not None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        if path == "/login":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(login_page().encode("utf-8"))
            return

        if path == "/logout":
            # remove cookie
            self.send_response(303)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "session=; Max-Age=0; Path=/")
            self.end_headers()
            return

        if not self.is_authenticated():
            # if someone goes directly to /proxies.txt, send them to login too
            self.redirect("/login")
            return

        if path in ("/", "/dashboard", "/proxies.txt"):
            page = dashboard_page()
        elif path == "/clients":
            page = clients_page()
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode("utf-8"))

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8")
        form = urllib.parse.parse_qs(body)

        if path == "/login":
            username = form.get("username", [""])[0]
            password = form.get("password", [""])[0]
            if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
                session_id = secrets.token_hex(16)
                SESSIONS[session_id] = username
                self.send_response(303)
                self.send_header("Location", "/dashboard")
                self.send_header(
                    "Set-Cookie",
                    f"session={session_id}; HttpOnly; Path=/",
                )
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(login_page("Invalid credentials").encode("utf-8"))
            return

        # other POST routes require authentication
        if not self.is_authenticated():
            self.redirect("/login")
            return

        if path == "/create_client":
            name = form.get("name", [""])[0].strip()
            count_str = form.get("count", ["0"])[0].strip()
            try:
                count = int(count_str)
            except ValueError:
                count = 0

            if not name or count <= 0:
                page = clients_page(error="Please provide a valid name and a positive number of proxies.")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(page.encode("utf-8"))
                return

            db = load_db()
            all_proxies = read_all_proxies()
            assigned_set = set(db.get("assigned", []))
            free_proxies = [p for p in all_proxies if p not in assigned_set]

            if len(free_proxies) < count:
                page = clients_page(error=f"Not enough free proxies. Requested {count}, available {len(free_proxies)}.")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(page.encode("utf-8"))
                return

            allocated = free_proxies[:count]
            db.setdefault("assigned", [])
            db["assigned"].extend(allocated)

            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            safe_name = "".join(c for c in name if c.isalnum() or c in ("-", "_"))
            if not safe_name:
                safe_name = "client"
            filename = f"{count}_proxies_{safe_name}.txt"

            client_entry = {
                "name": name,
                "created_at": timestamp,
                "filename": filename,
                "proxies": allocated,
            }
            db.setdefault("clients", [])
            db["clients"].append(client_entry)
            save_db()

            # Return the file as a download
            content = "\n".join(allocated) + "\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))
            return

        # unknown POST
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Not found")


def run_server(host, port):
    load_db()
    with socketserver.TCPServer((host, port), ProxyPanelHandler) as httpd:
        print(f"Proxy panel running on http://{host}:{port}")
        httpd.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simple proxy management panel")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=1991, help="TCP port (default 1991)")
    args = parser.parse_args()
    run_server(args.host, args.port)
