from flask import Flask, render_template, request, send_from_directory, jsonify, redirect, url_for, session
from flask_socketio import SocketIO, emit
import os
import uuid
import json

UPLOAD_FOLDER = 'uploads'
CHAT_FILE = 'chat_history.json'
CHAT_PASSWORD = "adminchat123"  # Change this to your desired password

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'  # Must be secret in real usage
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
socketio = SocketIO(app)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# Load history
if os.path.exists(CHAT_FILE):
    with open(CHAT_FILE, 'r', encoding='utf-8') as f:
        messages = json.load(f)
else:
    messages = []

# Ensure every old message has an ID
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

@app.route('/')
def index():
    return render_template('chat.html')

@app.route('/messages')
def get_messages():
    return jsonify(messages)

@socketio.on('message')
def handle_message(data):
    # Expect data to be { username: '...', text: '...' }
    message_id = str(uuid.uuid4())
    message_data = {
        "id": message_id,
        "username": data.get("username", "Anonymous"),
        "text": data.get("text", ""),
        "type": "text"
    }
    messages.append(message_data)
    save_messages()
    emit('message', message_data, broadcast=True)


@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files['file']
    username = request.form.get("username", "no name")
    if file:
        filename = str(uuid.uuid4()) + "_" + file.filename
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
        socketio.emit('file_message', message_data, broadcast=True)
    return "OK"

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=False)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == CHAT_PASSWORD:
            session['authenticated'] = True
            return redirect(url_for('index'))
        else:
            return "<h3 style='color:red;'>Wrong password!</h3><a href='/login'>Try again</a>"
    return '''
        <form method="POST" style="display:flex; flex-direction:column; width:300px; margin:100px auto; font-family:Arial;">
            <h2>🔒 Enter Chat Password</h2>
            <input type="password" name="password" placeholder="Password" required style="padding:8px; margin-bottom:10px;">
            <button type="submit" style="padding:8px;">Login</button>
        </form>
    '''

@app.before_request
def require_login():
    if request.endpoint not in ('login', 'static') and not session.get('authenticated'):
        return redirect(url_for('login'))

@socketio.on('delete_message')
def handle_delete(message_id):
    global messages
    msg_to_delete = next((m for m in messages if m['id'] == message_id), None)
    if msg_to_delete:
        if msg_to_delete.get("type") == "file" and "stored_name" in msg_to_delete:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], msg_to_delete["stored_name"])
            if os.path.exists(file_path):
                os.remove(file_path)
        messages = [m for m in messages if m['id'] != message_id]
        save_messages()
        emit('delete_message', message_id, broadcast=True)

if __name__ == '__main__':
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
