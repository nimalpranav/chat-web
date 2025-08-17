from flask import Flask, render_template, request, send_from_directory, jsonify, redirect, url_for, session, abort
from flask_socketio import SocketIO, emit, disconnect
import os, uuid, json, time
import csv
import datetime

UPLOAD_FOLDER = 'uploads'
CHAT_FILE = 'chat_history.json'
MESSAGES_CSV = 'chat_history.json'
CHAT_PASSWORD = "red123"          # chat access
ADMIN_PIN = "nimalpranavandkousek"           # admin login pin
banned_users = set()
messages = []
admins = {"admin": "admin123"}

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
banned_users = set()     # usernames
muted_until = {}         # sid -> epoch seconds
chat_locked = False
pinned_message = None    # {text, by, ts} or None

# --------- pages ---------
@app.route("/")
def home():
    return redirect(url_for("login"))

@app.route("/chat")
def chat():
    if not session.get("authenticated"):   # fixed
        return redirect(url_for("login"))
    return render_template("chat.html", messages=messages)

@app.route("/delete/<message_id>", methods=["POST"])
def delete_message(message_id):
    if not session.get("authenticated"):
        return redirect(url_for("login"))

    global messages
    messages = [m for m in messages if m['id'] != message_id]
    save_messages()
    socketio.emit('delete_message', message_id)  # optional: live update for clients
    return redirect(url_for("chat"))


@app.route('/messages')
def get_messages():
    return jsonify(messages)

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=False)

@app.route("/admin/unban/<username>")
def unban_user(username):
    admin_required()
    if username in banned_users:
        banned_users.remove(username)
        socketio.emit('system', {'text': f'{username} was unbanned.'})
        return f"{username} has been unbanned."
    else:
        return f"{username} is not banned."

@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files['file']
    username = request.form.get("username", "no name")
    if file:
        filename = f"{uuid.uuid4()}_{file.filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        file_url = f"/download/{filename}"
        message_data = {
            "id": str(uuid.uuid4()),
            "filename": file.filename,
            "url": file_url,
            "stored_name": filename,
            "username": username,
            "type": "file"
        }
        messages.append(message_data)
        save_messages()
        socketio.emit('file_message', message_data, )
    return "OK"

# --------- chat login (PIN) ---------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == CHAT_PASSWORD:
            session['authenticated'] = True
            return redirect(url_for('chat'))
        return "<h3 style='color:red;'>Wrong password!</h3><a href='/login'>Try again</a>"
    return '''
    <form method="POST" style="display:flex; flex-direction:column; width:300px; margin:100px auto; font-family:Arial;">
        <h2>🔒 Enter Chat Password</h2>
        <input type="password" name="password" placeholder="Password" required style="padding:8px; margin-bottom:10px;">
        <button type="submit" style="padding:8px;">Login</button>
    </form>'''

@app.before_request
def require_login():
    """
    Allow all /admin* routes without the chat PIN (they still require admin PIN inside),
    and always allow /login and /static.
    """
    # Allow admin routes through; they'll be gated by admin PIN below
    if request.path.startswith('/admin'):
        return None
    # Allow chat login + static
    if request.endpoint in ('login', 'static'):
        return None
    # For everything else, require chat PIN
    if not session.get('authenticated'):
        return redirect(url_for('login'))

# --------- Admin login + dashboard ---------
def admin_required():
    if not session.get('is_admin'):
        abort(403)

@app.route('/admin', methods=['GET'])
def admin_home():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
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
      <p><a href="/admin/logout">Log out (admin)</a></p>
    </div>
    """


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

# --- Admin actions ---
@app.post('/admin/lock')
def admin_lock():
    admin_required()
    global chat_locked
    chat_locked = (request.form.get('state') == 'lock')
    socketio.emit('lock_state', {'locked': chat_locked})
    return redirect(url_for('admin_home'))

@app.post('/admin/pin')
def admin_pin():
    admin_required()
    global pinned_message
    text = (request.form.get('text') or '').strip()
    if text:
        pinned_message = {'text': text, 'by': 'admin', 'ts': time.time()}
        socketio.emit('pinned', pinned_message)
    return redirect(url_for('admin_home'))

@app.get('/admin/unpin')
def admin_unpin():
    admin_required()
    global pinned_message
    pinned_message = None
    socketio.emit('pinned', None)
    return redirect(url_for('admin_home'))

@app.post('/admin/kick')
def admin_kick():
    admin_required()
    username = (request.form.get('username') or '').strip()
    sid = user_to_sid.get(username)
    if sid:
        socketio.emit('system', {'text': f'{username} was kicked by admin.'})
        # include namespace when disconnecting from HTTP context
        disconnect(sid=sid, namespace='/')
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

def save_message(sender, text):
    messages = load_messages()
    message_id = len(messages) + 1
    with open(MESSAGES_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([message_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sender, text])

def load_messages():
    if not os.path.exists(MESSAGES_CSV):
        return []
    with open(MESSAGES_CSV, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        return list(reader)  # Each row = [id, timestamp, sender, text]

@app.post('/admin/mute')
def admin_mute():
    admin_required()
    username = (request.form.get('username') or '').strip()
    try:
        seconds = int(request.form.get('seconds') or 60)
    except ValueError:
        seconds = 60
    seconds = max(5, seconds)
    sid = user_to_sid.get(username)
    if sid:
        until = time.time() + seconds
        muted_until[sid] = until
        socketio.emit('muted', {'until': until, 'username': username}, to=sid)
        socketio.emit('system', {'text': f'{username} is muted for {seconds}s.'}, )
    return redirect(url_for('admin_home'))

@app.post('/admin/unmute')
def admin_unmute():
    admin_required()
    username = (request.form.get('username') or '').strip()
    sid = user_to_sid.get(username)
    if sid and sid in muted_until:
        muted_until.pop(sid, None)
        socketio.emit('muted', {'until': 0, 'username': username}, to=sid)
        socketio.emit('system', {'text': f'{username} is unmuted.'})
    return redirect(url_for('admin_home'))


# --------- Socket.IO events ----------
from flask import request  # ensure request is available here

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
    broadcast_online()

def broadcast_online():
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
    broadcast_online()

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
    is_dm = bool(data.get('to'))
    target_user = (data.get('to') or '').strip()

    message_id = str(uuid.uuid4())
    message_data = {
        "id": message_id,
        "username": username,
        "text": text,
        "type": "text",
        "dm": target_user if is_dm else None
    }

    if not is_dm:
        messages.append(message_data)
        save_messages()

    if is_dm:
        target_sid = user_to_sid.get(target_user)
        if not target_sid:
            emit('system', {'text': f'User {target_user} not online.'})
            return
        emit('message', message_data, to=target_sid)
        emit('message', message_data, to=sid)
    else:
        socketio.emit('message', message_data)

# --- run ---
if __name__ == '__main__':
    # change host/port as needed; remove debug in production
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)


