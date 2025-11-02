import os
import uuid
import threading
import time
from flask import Flask, request, render_template_string, flash, redirect, session, url_for
from instagrapi import Client
from instagrapi.exceptions import TwoFactorRequired
from werkzeug.utils import secure_filename

# --- Config ---
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change_this_secret")
# Render typically sets PORT=10000; use that for local dev too
PORT = int(os.environ.get("PORT", 10000))

# persistent sessions dir (use Render persistent disk at /sessions if you mounted one)
SESS_DIR = os.environ.get("SESS_DIR", "sessions")
os.makedirs(SESS_DIR, exist_ok=True)

# task tracking
task_status = {}   # {task_id: {"should_stop": False}}
task_errors = {}   # {task_id: [ "log lines..." ]}
pending_creds = {} # {token: {"username":..., "password":...}}  for challenge flow

# --- HTML (use your provided UI) ---
HTML_FORM = '''
<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {margin:0;padding:0;min-height:100vh;background:linear-gradient(to top,#f5e1ff,#fff);font-family:Segoe UI,Arial,sans-serif;display:flex;flex-direction:column;align-items:center;}
    .container{background:#fff;max-width:400px;width:94vw;margin:32px auto 0 auto;padding:24px 18px 18px 18px;border-radius:18px;box-shadow:0 8px 36px 0 rgba(80,0,200,0.10);}
    label{display:block;font-weight:600;margin-bottom:5px;margin-top:15px;color:#5619e9;letter-spacing:.6px;}
    input,select{width:100%;box-sizing:border-box;padding:10px;margin-bottom:13px;border-radius:7px;border:1.3px solid #e0c7fc;font-size:15px;outline:none;background:#fffb8e;transition:border-color 0.2s;}
    input[type=file]{background:#fff;border:none;margin-bottom:9px;}
    input:focus,select:focus{border-color:#b78cff;}
    button{width:100%;padding:12px 0;background:linear-gradient(to right,#b05cff,#ffa4fc);color:#fff;font-size:17px;font-weight:650;border-radius:7px;border:none; margin-top:20px;letter-spacing:1px;box-shadow:0 4px 12px 0 rgba(80,0,200,0.08);cursor:pointer;}
    .stop-container{background:#fff0fa;margin-top:32px;max-width:400px;width:94vw;border-radius:18px;box-shadow:0 8px 36px 0 rgba(160,0,100,0.07);padding:18px 18px 16px 18px;display:flex;flex-direction:column;align-items:stretch;}
    .stop-container label{color:#d12f8a;margin-top:5px;}
    .stop-container button{background:linear-gradient(to right,#e663e0,#ff6da0);margin-top:10px;}
    @media(max-width:520px){.container,.stop-container{padding:12px 4vw 16px 4vw;margin:11px 2vw 0 2vw;}input,select{font-size:14px;padding:9px;}button{font-size:15px;padding:11px 0;}}
    .error-log{background:#ffe0e0;color:#b60000;margin:20px auto;max-width:400px;width:94vw;padding:12px 16px;border-radius:12px;font-size:15px;}
    .info{background:#e8f4ff;color:#034; margin:12px auto;padding:10px;border-radius:8px;max-width:400px;width:94vw;}
  </style>
</head>
<body>
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <div class="info">
      <ul>
        {% for message in messages %}
          <li>{{ message }}</li>
        {% endfor %}
      </ul>
      </div>
    {% endif %}
  {% endwith %}

  <form class="container" action="/send" method="POST" enctype="multipart/form-data">
    <label>Instagram Username:</label>
    <input type="text" name="username" placeholder="Enter your username" required>
    <label>Instagram Password:</label>
    <input type="password" name="password" placeholder="Enter your password" required>
    <label>Send To:</label>
    <select name="send_to">
      <option value="inbox">Inbox</option>
      <option value="group">Group</option>
    </select>
    <label>Target Username (for Inbox):</label>
    <input type="text" name="target_username" placeholder="Enter target username">
    <label>Thread ID (for Group):</label>
    <input type="text" name="thread_id" placeholder="Enter group thread ID">
    <label>Haters Name:</label>
    <input type="text" name="hater_name" placeholder="Enter hater's name">
    <label>Message File:</label>
    <input type="file" name="msg_file">
    <label>Delay (seconds):</label>
    <input type="number" name="delay" placeholder="Enter delay in seconds">

    {% if session.get('challenge_required') %}
      <label>Enter 2FA/Challenge Code:</label>
      <input type="text" name="challenge_code" required>
    {% endif %}

    <button type="submit">Send Messages</button>
  </form>

  <form class="stop-container" action="/stop" method="POST">
    <label>Stop Messaging (by Start ID):</label>
    <input type="text" name="stop_id" placeholder="Enter your Start ID" required>
    <button type="submit">Stop</button>
  </form>

  {% if error_log %}
  <div class="error-log">
    <b>Last task error/status log:</b><br>
    {% for msg in error_log %}
      {{msg}}<br>
    {% endfor %}
  </div>
  {% endif %}

</body>
</html>
'''

# --- Helpers ---
def session_path_for(username: str):
    safe = secure_filename(username)
    return os.path.join(SESS_DIR, f"{safe}_session.json")

def try_login_and_save(username: str, password: str, log_list: list):
    """
    Try to load saved session or login fresh. Returns (client, statusstr).
    statusstr: "ok", "challenge", or "error"
    """
    cl = Client()
    path = session_path_for(username)
    # try load saved session
    if os.path.exists(path):
        try:
            cl.load_settings(path)
            # verify: .login() with no password may be not necessary; but call login to ensure valid session
            # Some versions allow cl.login() without password to re-auth using cookie settings.
            try:
                cl.login(username, password)  # will succeed if session is fine; else raise
            except Exception:
                # If login with password fails after load, we still may have valid session; try just accessing user info
                try:
                    _ = cl.user_id
                except Exception:
                    raise
            log_list.append(f"[{username}] Loaded saved session.")
            return cl, "ok"
        except Exception as e:
            # corrupted/expired session -> remove and proceed to fresh login attempt
            log_list.append(f"[{username}] Saved session invalid: {e}")
            try:
                os.remove(path)
            except Exception:
                pass

    # fresh login
    try:
        cl.login(username, password)
        cl.dump_settings(path)
        log_list.append(f"[{username}] Fresh login successful and session saved.")
        return cl, "ok"
    except TwoFactorRequired as tfe:
        log_list.append(f"[{username}] TwoFactor required: {tfe}")
        return None, "challenge"
    except Exception as e:
        text = str(e).lower()
        # detect challenge / verification words
        if "challenge" in text or "verification" in text or "checkpoint" in text or "security code" in text:
            log_list.append(f"[{username}] Challenge/verification required: {e}")
            return None, "challenge"
        log_list.append(f"[{username}] Login failed: {e}")
        return None, "error"

def start_send_thread(task_id, username, cl, send_to, target_username, thread_id, content_lines, delay):
    """
    cl is an authenticated Client instance (settings already saved).
    """
    def _worker():
        log = []
        try:
            if send_to == "inbox" and target_username:
                try:
                    user_id = cl.user_id_from_username(target_username)
                except Exception as e:
                    log.append(f"[{task_id}] Resolve username failed: {e}")
                    task_errors[task_id] = log
                    task_status.pop(task_id, None)
                    return
                for msg in content_lines:
                    if task_status[task_id]["should_stop"]:
                        log.append(f"[{task_id}] Messaging stopped by user.")
                        break
                    try:
                        cl.direct_send(msg, user_ids=[user_id])
                        log.append(f"[{task_id}] Sent to @{target_username}: {msg}")
                    except Exception as e:
                        log.append(f"[{task_id}] Send error for @{target_username}: {e}")
                    if delay:
                        time.sleep(int(delay))
            elif send_to == "group" and thread_id:
                for msg in content_lines:
                    if task_status[task_id]["should_stop"]:
                        log.append(f"[{task_id}] Messaging stopped by user.")
                        break
                    try:
                        cl.direct_send(msg, thread_ids=[thread_id])
                        log.append(f"[{task_id}] Sent to group {thread_id}: {msg}")
                    except Exception as e:
                        log.append(f"[{task_id}] Send error for group {thread_id}: {e}")
                    if delay:
                        time.sleep(int(delay))
            else:
                log.append(f"[{task_id}] Nothing to send (bad params).")
        except Exception as e:
            log.append(f"[{task_id}] Unexpected error: {e}")
        log.append(f"[{task_id}] Finished.")
        task_errors[task_id] = log
        task_status.pop(task_id, None)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


# --- Routes ---
@app.route("/", methods=["GET"])
def index():
    # show last error log if ?logid= passed
    logid = request.args.get("logid")
    err = None
    if logid and logid in task_errors:
        err = task_errors[logid]
    return render_template_string(HTML_FORM, error_log=err)

@app.route("/send", methods=["POST"])
def send():
    """
    Flow:
    1) If session has 'challenge_token' and user provided challenge_code -> attempt resolve & login
    2) Else: attempt login/load-session. If ok -> start thread. If challenge -> set challenge_token in session and ask user to provide code.
    """
    username = request.form.get("username")
    password = request.form.get("password")
    send_to = request.form.get("send_to")
    target_username = request.form.get("target_username")
    thread_id = request.form.get("thread_id")
    delay = request.form.get("delay")
    msg_file = request.files.get("msg_file")
    challenge_code = request.form.get("challenge_code")

    # parse content lines
    if msg_file and msg_file.filename:
        try:
            raw = msg_file.read()
            content_lines = raw.decode("utf-8").splitlines()
        except Exception:
            content_lines = [ "Hello from bot!" ]
    else:
        content_lines = [ "Hello from bot!" ]

    # If user is submitting challenge code (session should have token)
    if session.get("challenge_token") and challenge_code:
        token = session.get("challenge_token")
        creds = pending_creds.get(token)
        if not creds:
            flash("No pending challenge found. Please try login again.")
            session.pop("challenge_token", None)
            session.pop("challenge_required", None)
            return redirect(url_for("index"))
        # attempt to resolve challenge
        log = []
        cl = Client()
        try:
            # try load settings if exists (some flows store challenge state in settings)
            path = session_path_for(creds["username"])
            if os.path.exists(path):
                try:
                    cl.load_settings(path)
                except Exception:
                    pass
            # attempt login and challenge resolve
            try:
                cl.login(creds["username"], creds["password"])
            except Exception:
                # ignore; challenge resolve may still accept code
                pass
            # try to resolve challenge with provided code
            try:
                # many instagrapi versions use `challenge_resolve` or `two_factor_login`
                # try challenge_resolve first
                cl.challenge_resolve(challenge_code)
            except Exception:
                try:
                    # fallback: two-factor enter
                    cl.two_factor_login(challenge_code)
                except Exception as e:
                    # if fails, show error
                    flash(f"Challenge resolution failed: {e}")
                    return redirect(url_for("index"))

            # if we reached here, dump settings and continue to sending
            try:
                cl.dump_settings(session_path_for(creds["username"]))
            except Exception:
                pass

            # start task immediately
            task_id = str(uuid.uuid4())
            task_status[task_id] = {"should_stop": False}
            start_send_thread(task_id, creds["username"], cl, send_to, target_username, thread_id, content_lines, delay)
            # cleanup
            pending_creds.pop(token, None)
            session.pop("challenge_token", None)
            session.pop("challenge_required", None)
            flash(f"Started! Your Start ID is: {task_id}")
            return redirect(url_for("index", logid=task_id))
        except Exception as e:
            flash(f"Challenge attempt error: {e}")
            return redirect(url_for("index"))

    # Normal login flow
    if not username or not password:
        flash("Username and password required.")
        return redirect(url_for("index"))

    log = []
    cl, status = try_login_and_save(username, password, log)
    # store log into a short-lived task_errors entry so user can inspect
    temp_logid = str(uuid.uuid4())[:8]
    task_errors[temp_logid] = log

    if status == "ok" and cl:
        # start background task
        task_id = str(uuid.uuid4())
        task_status[task_id] = {"should_stop": False}
        start_send_thread(task_id, username, cl, send_to, target_username, thread_id, content_lines, delay)
        flash(f"Started! Your Start ID is: {task_id}")
        return redirect(url_for("index", logid=task_id))
    elif status == "challenge":
        # create pending token and ask user to submit challenge code
        token = str(uuid.uuid4())
        pending_creds[token] = {"username": username, "password": password}
        session["challenge_token"] = token
        session["challenge_required"] = True
        flash("Instagram requires verification. Enter the code sent to your device in the form and submit.")
        return redirect(url_for("index"))
    else:
        # login error
        flash("Login failed. See details below.")
        return redirect(url_for("index", logid=temp_logid))

@app.route("/stop", methods=["POST"])
def stop():
    stop_id = request.form.get("stop_id")
    if not stop_id:
        flash("Please provide Start ID.")
        return redirect(url_for("index"))
    if stop_id in task_status:
        task_status[stop_id]["should_stop"] = True
        flash(f"Stopped messaging for ID: {stop_id}")
    else:
        flash("Invalid Start ID or no running task found.")
    return redirect(url_for("index"))

# --- Run server ---
if __name__ == "__main__":
    # local testing: runs on PORT (default 10000)
    app.run(host="0.0.0.0", port=PORT)
