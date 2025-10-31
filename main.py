from flask import Flask, render_template_string, request, redirect, flash, session
from instagrapi import Client
import os

app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['SESSION_TYPE'] = 'filesystem'

HTML_FORM = '''
<form action="/" method="POST" enctype="multipart/form-data">
  <label>Instagram Username:</label>
  <input type="text" name="username" style="background:yellow;" required><br><br>
  
  <label>Instagram Password:</label>
  <input type="password" name="password" style="background:yellow;" required><br><br>
  
  <label>Send To:</label>
  <select name="send_to" style="background:yellow;">
    <option value="inbox">Inbox</option>
    <option value="group">Group</option>
  </select><br><br>
  
  <label>Target Username (for Inbox):</label>
  <input type="text" name="target_username" style="background:yellow;"><br><br>
  
  <label>Thread ID (for Group):</label>
  <input type="text" name="thread_id" style="background:yellow;"><br><br>
  
  <label>Haters Name:</label>
  <input type="text" name="hater_name" style="background:yellow;"><br><br>
  
  <label>Message File:</label>
  <input type="file" name="msg_file"><br><br>
  
  <label>Delay (seconds):</label>
  <input type="number" name="delay" style="background:yellow;"><br><br>

  {% if session.get('challenge_required') %}
    <label>Enter 2FA/Challenge Code:</label>
    <input type="text" name="challenge_code" style="background:yellow;" required><br><br>
  {% endif %}
  
  <button type="submit" style="background:pink;color:blue;font-size:18px;">Send Messages</button>
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
'''

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
        
        if msg_file:
            content = msg_file.read().decode("utf-8").splitlines()
        else:
            content = ["Hello from Flask demo!"]
        
        cl = Client()
        
        try:
            # अगर 2FA वैरिएबल session में है तो कोड के साथ challenge पूरा करें
            if session.get('challenge_required'):
                cl.challenge_resolve(challenge_code)
                session.pop('challenge_required')
                flash("Challenge code accepted, retry submit form.")
                return redirect("/")
            else:
                cl.login(username, password)
                
                if send_to == "inbox" and target_username:
                    user_id = cl.user_id_from_username(target_username)
                    for msg in content:
                        cl.direct_send(msg, user_ids=[user_id])
                elif send_to == "group" and thread_id:
                    for msg in content:
                        cl.direct_send(msg, thread_ids=[thread_id])
                flash("Message(s) sent successfully!")
        except Exception as e:
            # अगर challenge या 2FA मांगा गया हो तो यूजर से कोड मांगो
            if 'challenge' in str(e).lower() or 'two_factor' in str(e).lower():
                session['challenge_required'] = True
                flash("Instagram Challenge or 2FA code required. Please enter the code and submit again.")
                return redirect("/")
            else:
                flash(f"Failed: {e}")
        
        return redirect("/")
    return render_template_string(HTML_FORM)

if __name__ == "__main__":
    app.run(debug=True)
