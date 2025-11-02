from flask import Flask, render_template_string, request, redirect, flash, session
from instagrapi import Client
import threading
import uuid
import os
import time
import json

app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['SESSION_TYPE'] = 'filesystem'

task_status = {}
task_errors = {}

HTML_FORM = '''  <!-- same HTML as before -->  '''  # <-- apna original HTML_FORM yahan rahega (no change)


# 🔹 Utility: save/load session for each username
def get_session_path(username):
    os.makedirs("sessions", exist_ok=True)
    return f"sessions/{username}_session.json"


def login_with_session(cl, username, password, log):
    """Try loading old session, else login fresh."""
    session_path = get_session_path(username)
    if os.path.exists(session_path):
        try:
            cl.load_settings(session_path)
            cl.login(username, password)
            log.append(f"[{username}] Logged in via saved session ✅")
            return True
        except Exception as e:
            log.append(f"[{username}] Session load failed: {e} ❌ — retrying fresh login")
            os.remove(session_path)
    try:
        cl.login(username, password)
        cl.dump_settings(session_path)
        log.append(f"[{username}] Fresh login successful & session saved ✅")
        return True
    except Exception as e:
        log.append(f"[{username}] Fresh login failed ❌: {e}")
        return False


def send_messages_task(task_id, username, password, send_to, target_username, thread_id, content, delay, challenge_code=None):
    log = []
    cl = Client()

    # login attempt with session support
    if not login_with_session(cl, username, password, log):
        task_errors[task_id] = log
        task_status.pop(task_id, None)
        return

    try:
        if send_to == "inbox" and target_username:
            user_id = cl.user_id_from_username(target_username)
            for msg in content:
                if task_status[task_id]["should_stop"]:
                    log.append(f"[{task_id}] Messaging stopped by user.")
                    break
                try:
                    cl.direct_send(msg, user_ids=[user_id])
                    log.append(f"[{task_id}] Sent to {target_username}: {msg}")
                except Exception as e:
                    log.append(f"[{task_id}] Send error: {e}")
                if delay:
                    time.sleep(int(delay))
        elif send_to == "group" and thread_id:
            for msg in content:
                if task_status[task_id]["should_stop"]:
                    log.append(f"[{task_id}] Messaging stopped by user.")
                    break
                try:
                    cl.direct_send(msg, thread_ids=[thread_id])
                    log.append(f"[{task_id}] Sent to group thread {thread_id}: {msg}")
                except Exception as e:
                    log.append(f"[{task_id}] Send error: {e}")
                if delay:
                    time.sleep(int(delay))
        log.append(f"[{task_id}] ✅ Messaging operation finished.")
    except Exception as e:
        log.append(f"[{task_id}] General send error: {e}")

    task_errors[task_id] = log
    task_status.pop(task_id, None)


@app.route("/", methods=["GET", "POST"])
def index():
    error_log = None
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        send_to = request.form.get("send_to")
        target_username = request.form.get("target_username")
        thread_id = request.form.get("thread_id")
        delay = request.form.get("delay")
        msg_file = request.files.get("msg_file")
        challenge_code = request.form.get("challenge_code")

        if msg_file:
            content = msg_file.read().decode("utf-8").splitlines()
        else:
            content = ["Hello from Flask demo!"]

        task_id = str(uuid.uuid4())
        task_status[task_id] = {"should_stop": False}
        thread = threading.Thread(
            target=send_messages_task,
            args=(task_id, username, password, send_to, target_username, thread_id, content, delay, challenge_code),
        )
        thread.start()
        flash(f"Started! Your Start ID is: {task_id}")
        return redirect(f"/?logid={task_id}")

    l_id = request.args.get("logid")
    if l_id and l_id in task_errors:
        error_log = task_errors[l_id]
    return render_template_string(HTML_FORM, error_log=error_log)


@app.route("/stop", methods=["POST"])
def stop_message():
    stop_id = request.form.get("stop_id")
    if stop_id in task_status:
        task_status[stop_id]["should_stop"] = True
        flash(f"Stopped messaging for ID: {stop_id}")
    else:
        flash("Invalid Start ID or no such running messaging task found.")
    return redirect("/")


if __name__ == "__main__":
    app.run(debug=True)
