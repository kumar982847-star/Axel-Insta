from flask import Flask, render_template_string, request, redirect, flash, session
from instagrapi import Client
import threading
import uuid

app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['SESSION_TYPE'] = 'filesystem'

# Track task status by ID
task_status = {}  # Example: { "id123": {"should_stop": False} }

HTML_FORM = '''
<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {margin:0; padding:0; min-height:100vh; background:linear-gradient(to top,#f5e1ff,#fff);font-family:Segoe UI,Arial,sans-serif;display:flex;flex-direction:column;align-items:center;}
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
  </style>
</head>
<body>
  <form class="container" action="/" method="POST" enctype="multipart/form-data">
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
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <ul>
        {% for message in messages %}
          <li>{{ message }}</li>
        {% endfor %}
      </ul>
    {% endif %}
  {% endwith %}
</body>
</html>
'''

def send_messages_task(task_id, username, password, send_to, target_username, thread_id, content, delay, challenge_code=None):
    cl = Client()
    try:
        if challenge_code:
            cl.challenge_resolve(challenge_code)
        cl.login(username, password)
    except Exception as e:
        flash(f"[{task_id}] Login error: {e}")
        task_status.pop(task_id, None)
        return
    try:
        if send_to == "inbox" and target_username:
            user_id = cl.user_id_from_username(target_username)
            for msg in content:
                if task_status[task_id]["should_stop"]:
                    flash(f"[{task_id}] Messaging stopped by user.")
                    break
                res = cl.direct_send(msg, user_ids=[user_id])
                if not res:
                    flash(f"[{task_id}] Message send failed: {msg}")
                if delay:
                    import time
                    time.sleep(int(delay))
        elif send_to == "group" and thread_id:
            for msg in content:
                if task_status[task_id]["should_stop"]:
                    flash(f"[{task_id}] Messaging stopped by user.")
                    break
                res = cl.direct_send(msg, thread_ids=[thread_id])
                if not res:
                    flash(f"[{task_id}] Message send failed: {msg}")
                if delay:
                    import time
                    time.sleep(int(delay))
        flash(f"[{task_id}] Messaging operation finished.")
    except Exception as e:
        flash(f"[{task_id}] Send error: {e}")
    task_status.pop(task_id, None)

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        send_to = request.form.get("send_to")
        target_username = request.form.get("target_username")
        thread_id = request.form.get("thread_id")
        delay = request.form.get("delay")
        msg_file = request.files.get("msg_file")
        challenge_code = request.form.get("challenge_code")
        # Read message content from file, or use default/demo text
        if msg_file:
            content = msg_file.read().decode("utf-8").splitlines()
        else:
            content = ["Hello from Flask demo!"]
        # New unique ID for this sending task
        task_id = str(uuid.uuid4())
        task_status[task_id] = {"should_stop": False}
        # Run sending in a background thread so Flask remains responsive
        thread = threading.Thread(target=send_messages_task,
                                  args=(task_id, username, password, send_to, target_username, thread_id, content, delay, challenge_code))
        thread.start()
        flash(f"Started! Your Start ID is: {task_id}")
        return redirect("/")
    return render_template_string(HTML_FORM)

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
