from flask import Flask, render_template, request, send_from_directory, jsonify
from flask_socketio import SocketIO, emit
import os
import uuid
import json

UPLOAD_FOLDER = 'uploads'
CHAT_FILE = 'chat_history.json'

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
socketio = SocketIO(app)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Load history
if os.path.exists(CHAT_FILE):
    with open(CHAT_FILE, 'r', encoding='utf-8') as f:
        messages = json.load(f)
else:
    messages = []

# 🔧 Backfill IDs for any older messages that might not have one
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
def handle_message(msg):
    message_id = str(uuid.uuid4())
    message_data = {"id": message_id, "text": msg, "type": "text"}
    messages.append(message_data)
    save_messages()
    emit('message', message_data, broadcast=True)

@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files['file']
    if file:
        filename = str(uuid.uuid4()) + "_" + file.filename  # stored name
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        file_url = f"/download/{filename}"
        message_data = {
            "id": str(uuid.uuid4()),
            "filename": file.filename,   # original name for display
            "url": file_url,
            "stored_name": filename,     # actual stored name for deletion
            "type": "file"
        }
        messages.append(message_data)
        save_messages()
        socketio.emit('file_message', message_data, broadcast=True)
    return "OK"

@app.route('/download/<filename>')
def download_file(filename):
    # Inline preview (don’t force download)
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=False)

@socketio.on('delete_message')
def handle_delete(message_id):
    global messages
    msg_to_delete = next((m for m in messages if m['id'] == message_id), None)
    if msg_to_delete:
        # If it's a file message, delete the stored file too
        if msg_to_delete.get("type") == "file" and "stored_name" in msg_to_delete:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], msg_to_delete["stored_name"])
            if os.path.exists(file_path):
                os.remove(file_path)
        messages = [m for m in messages if m['id'] != message_id]
        save_messages()
        emit('delete_message', message_id, broadcast=True)

if __name__ == '__main__':
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
