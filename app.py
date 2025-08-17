from flask import Flask, render_template, request, send_from_directory, jsonify, redirect, url_for, session, abort, flash
from flask_socketio import SocketIO, emit, disconnect
import os, uuid, json, time
import csv
import datetime

UPLOAD_FOLDER = 'uploads'
CHAT_FILE = 'chat_history.json'
MESSAGES_CSV = 'chat_history.json'
CHAT_PASSWORD = "red123"          # chat access
ADMIN_PIN = "adminnimalandkousek" # admin login pin
MODERATOR_PASSWORD = "mod1234"        # moderator login pin
banned_users = set()
messages = []
admins = {"admin": "admin123"}
moderators = set()  # usernames of logged-in moderators
MODERATOR_PIN = "mod1234"
VALID_MODERATORS = set()
MOD_PIN = "mod1234"

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'  # change for production
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
socketio = SocketIO(app, cors_allowed_origins="*")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- persistence ---
if os.path.exists(CHAT_FILE):
    with open(CHAT_FILE, 'r', encoding='utf-8') as f:
        messages = json.load(f)
else:
    messages = []

def save_messages():
    with open(CHAT_FILE, 'w', encoding='utf-8') as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)

dirty = False
for m in messages:
    if 'id' not in m:
        m['id'] = str(uuid.uuid4())
        dirty = True
if dirty:
    save_messages()

# --- runtime state ---
connected = {}           # sid -> username
user_to_sid = {}         # username -> sid
muted_until = {}         # sid -> epoch seconds
chat_locked = False
pinned_message = None    # {text, by, ts} or None

# --------- pages ---------
@app.route("/")
def home():
    return redirect(url_for("login"))

@app.route("/moderator/login", methods=["GET", "POST"])
def moderator_login():
    if request.method == "POST":
        username = request.form.get("username")
        pin = request.form.get("pin")
        
        if username in VALID_MODERATORS and pin == MOD_PIN:
            session["authenticated"] = True      # ✅ allow them past require_login
            session["is_moderator"] = True       # ✅ mark as moderator
            session["username"] = username       # ✅ store name
            moderators.add(username)             # ✅ mark them online
            return redirect("/moderator")
        else:
            flash("Invalid username or PIN!", "error")
    
    return render_template("moderator_login.html")
    

@app.route("/chat")
def chat():
    if not session.get("authenticated"):
        return redirect(url_for("login"))
    return render_template("chat.html", messages=messages)

@app.route('/moderator')
def moderator_home():
    moderator_required()
    online = sorted(list(user_to_sid.keys()))
    return f"""
    <div>
      <h2>Moderator Panel</h2>
      <h3>Online Users</h3>
      <ul>{"".join(f"<li>{u}</li>" for u in online)}</ul>
      <h3>Actions</h3>
      <form method="POST" action="/admin/kick">
        <input name="username" placeholder="username">
        <button type="submit">Kick</button>
      </form>
      <form method="POST" action="/admin/mute">
        <input name="username" placeholder="username">
        <input name="seconds" type="number" value="60">
        <button type="submit">Mute</button>
      </form>
      <form method="POST" action="/admin/unmute">
        <input name="username" placeholder="username">
        <button type="submit">Unmute</button>
      </form>
      <p><a href="/moderator/logout">Log out (moderator)</a></p>
    </div>
    """

@app.route('/moderator/logout')
def moderator_logout():
    username = session.get('username')
    if username in moderators:
        moderators.discard(username)
    session.pop('is_moderator', None)
    session.pop('authenticated', None)   # ✅ clear auth
    session.pop('username', None)
    return redirect(url_for('moderator_login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == CHAT_PASSWORD:
            session['authenticated'] = True
            return redirect(url_for('chat'))

            session['authenticated'] = True
            session['role'] = 'moderator'
            return redirect(url_for('moderator_home'))
        return "<h3 style='color:red;'>Wrong password!</h3><a href='/login'>Try again</a>"
    
    return '''
    <form method="POST" style="display:flex; flex-direction:column; width:300px; margin:100px auto; font-family:Arial;">
        <h2>🔒 Enter Chat Password</h2>
        <input type="password" name="password" placeholder="Password" required style="padding:8px; margin-bottom:10px;">
        <button type="submit" style="padding:8px;">Login</button>
    </form>'''

@app.before_request
def require_login():
    # Allow admin routes; they'll require admin PIN inside
    if request.path.startswith('/admin'):
        return None
    if request.endpoint in ('login', 'static'):
        return None
    if not session.get('authenticated'):
        return redirect(url_for('login'))

# --------- Admin login + dashboard ---------
def admin_required():
    if not session.get('is_admin'):
        abort(403)
def moderator_required():
    if not session.get('is_moderator'):
        abort(403)


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        pin = request.form.get('pin')
        if pin == ADMIN_PIN:
            session['is_admin'] = True
            return redirect(url_for('admin_home'))
        return "<h3 style='color:red;'>Wrong admin PIN!</h3><a href='/admin/login'>Try again</a>"
    return '''
    <form method="POST" style="display:flex; flex-direction:column; width:300px; margin:100px auto; font-family:Arial;">
        <h2>🛡 Admin Login (PIN)</h2>
        <input type="password" name="pin" placeholder="Admin PIN" required style="padding:8px; margin-bottom:10px;">
        <button type="submit" style="padding:8px;">Login</button>
    </form>'''

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('admin_login'))

@app.route('/admin', methods=['GET'])
def admin_home():
    admin_required()
    online = sorted(list(user_to_sid.keys()))
    pinned = pinned_message['text'] if pinned_message else ''
    locked = chat_locked
    return f"""
    <div style="font-family:Arial; max-width:900px; margin:20px auto;">
      <h2>🛠 Admin Dashboard</h2>
      <p><b>Chat locked:</b> {locked}</p>
      <form method="POST" action="/admin/lock" style="margin-bottom:10px;">
        <button name="state" value="lock">Lock Chat</button>
        <button name="state" value="unlock">Unlock Chat</button>
      </form>
      <form method="POST" action="/admin/pin" style="margin-bottom:10px;">
        <input name="text" placeholder="Pin message..." style="width:300px;">
        <button type="submit">Pin</button>
        <a href="/admin/unpin">Unpin</a>
      </form>
      <h3>Online Users</h3>
      <ul>{"".join(f"<li>{u}</li>" for u in online) or "<i>none</i>"}</ul>
      <h3>Actions</h3>
      <form method="POST" action="/admin/kick" style="margin-bottom:6px;">
        <input name="username" placeholder="username">
        <button type="submit">Kick</button>
      </form>
      <form method="POST" action="/admin/ban" style="margin-bottom:6px;">
        <input name="username" placeholder="username">
        <button type="submit">Ban</button>
      </form>
      <form method="POST" action="/admin/mute" style="margin-bottom:6px;">
        <input name="username" placeholder="username">
        <input name="seconds" type="number" value="60" min="5" step="5" style="width:100px;">
        <button type="submit">Mute</button>
      </form>
      <form method="POST" action="/admin/unmute" style="margin-bottom:6px;">
        <input name="username" placeholder="username">
        <button type="submit">Unmute</button>
      </form>
      <p><b>Pinned:</b> {pinned}</p>
      <h3>Moderators</h3>
<form method="POST" action="/admin/promote">
    <input name="username" placeholder="username" required>
    <select name="action">
        <option value="promote">Promote to Moderator</option>
        <option value="demote">Demote</option>
    </select>
    <button type="submit">Submit</button>
</form>
<ul>
    Current Moderators: {", ".join(moderators) if moderators else "<i>none</i>"}
</ul>
      <p><a href="/admin/logout">Log out (admin)</a></p>
    </div>
    """

# --- Moderator helper ---
def can_kick_or_mute():
    """Return True if the user is admin or moderator."""
    if session.get('is_admin'):
        return True
    username = session.get('username')
    return username in moderators

# --- Admin/Moderator actions ---
@app.post('/admin/kick')
def admin_kick():
    if not can_kick_or_mute():
        return "Permission denied", 403
    username = (request.form.get('username') or '').strip()
    sid = user_to_sid.get(username)
    if sid:
        socketio.emit('system', {'text': f'{username} was kicked by {session.get("username")}'})
        disconnect(sid=sid, namespace='/')
    return redirect(url_for('admin_home'))

@app.post('/admin/promote')
def admin_promote():
    admin_required()  # only admins can promote/demote
    username = (request.form.get('username') or '').strip()
    action = request.form.get('action')

    if not username:
        flash("No username provided!", "error")
        return redirect(url_for('admin_home'))

    if action == 'promote':
        VALID_MODERATORS.add(username)
        moderators.add(username)
        socketio.emit('system', {'text': f'{username} is now a moderator.'})
        flash(f"{username} promoted to Moderator!", "success")

    elif action == 'demote':
        VALID_MODERATORS.discard(username)
        moderators.discard(username)
        socketio.emit('system', {'text': f'{username} is no longer a moderator.'})
        flash(f"{username} demoted!", "info")

    return redirect(url_for('admin_home'))

@app.post('/admin/ban')
def admin_ban():
    admin_required()
    username = (request.form.get('username') or '').strip()
    if username:
        banned_users.add(username)
        sid = user_to_sid.get(username)
        if sid:
            socketio.emit('system', {'text': f'{username} was banned.'})
            disconnect(sid=sid, namespace='/')
    return redirect(url_for('admin_home'))

@app.post('/admin/mute')
def admin_mute():
    if not can_kick_or_mute():
        return "Permission denied", 403
    username = (request.form.get('username') or '').strip()
    seconds = max(5, int(request.form.get('seconds') or 60))
    sid = user_to_sid.get(username)
    if sid:
        muted_until[sid] = time.time() + seconds
        socketio.emit('muted', {'until': time.time() + seconds, 'username': username}, to=sid)
        socketio.emit('system', {'text': f'{username} is muted for {seconds}s'})
    return redirect(url_for('admin_home'))

@app.post('/admin/unmute')
def admin_unmute():
    if not can_kick_or_mute():
        return "Permission denied", 403
    username = (request.form.get('username') or '').strip()
    sid = user_to_sid.get(username)
    if sid and sid in muted_until:
        muted_until.pop(sid, None)
        socketio.emit('muted', {'until': 0, 'username': username}, to=sid)
        socketio.emit('system', {'text': f'{username} is unmuted'})
    return redirect(url_for('admin_home'))

# --- Socket.IO events ---
@socketio.on('connect')
def on_connect():
    emit('lock_state', {'locked': chat_locked})
    if pinned_message:
        emit('pinned', pinned_message)

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    username = connected.pop(sid, None)
    if username and user_to_sid.get(username) == sid:
        user_to_sid.pop(username, None)
    socketio.emit('online_users', sorted(list(user_to_sid.keys())))

@socketio.on('set_username')
def set_username(data):
    username = (data.get('username') or 'no name').strip()
    if username in banned_users:
        emit('system', {'text': 'You are banned.'})
        disconnect()
        return
    sid = request.sid
    connected[sid] = username
    user_to_sid[username] = sid
    socketio.emit('online_users', sorted(list(user_to_sid.keys())))

@socketio.on('message')
def handle_message(data):
    sid = request.sid
    now = time.time()

    if sid in muted_until and muted_until[sid] > now:
        emit('system', {'text': 'You are muted.'})
        return
    if chat_locked:
        emit('system', {'text': 'Chat is locked by admin.'})
        return

    text = (data.get('text') or '').strip()
    if not text:
        return

    username = (data.get('username') or 'Anonymous').strip()
    message_id = str(uuid.uuid4())
    message_data = {"id": message_id, "username": username, "text": text, "type": "text"}
    messages.append(message_data)
    save_messages()
    socketio.emit('message', message_data)

# --- run ---
if __name__ == '__main__':
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
