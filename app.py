"""
WiFi Radar - Ana Uygulama
Flask + Socket.IO ile web sunucusu.
CSI sensing + ağ tarama + mesajlaşma entegrasyonu.
"""

import os
import sys
import threading
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit

# Modül yolunu ekle
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scanner.wifi_scanner import WiFiScanner
from scanner.messenger import NetworkMessenger
from scanner.csi.csi_manager import CSIManager

# ==================== UYGULAMA KURULUMU ====================
app = Flask(__name__)
app.config['SECRET_KEY'] = 'wifi-radar-secret-key'

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Modül örnekleri
scanner = WiFiScanner()
messenger = NetworkMessenger()
csi = CSIManager()

# CSI → SocketIO bridge
def _csi_update(data):
    """CSI durum değiştiğinde tüm web istemcilerine yayınla."""
    socketio.emit('csi_update', data)

csi.set_update_callback(_csi_update)

# ==================== WEB SAYFALARI ====================
@app.route('/')
def index():
    """Ana sayfa."""
    return render_template('index.html')


# ==================== API - DURUM ====================
@app.route('/api/status')
def api_status():
    """Sistem durum bilgisi."""
    scan_stats = scanner.get_statistics()
    msg_stats = messenger.get_statistics()
    csi_state = csi.get_current_state()
    
    return jsonify({
        "status": "active",
        "network": scan_stats.get("network", ""),
        "local_ip": scan_stats.get("local_ip", ""),
        "total_devices": scan_stats.get("total_devices", 0),
        "csi_mode": csi.mode,
        "csi_connected": csi.reader.is_connected,
        "csi_streaming": csi.reader.is_streaming,
        "csi_port": csi.reader.port,
        "presence_state": csi_state.get("presence", {}).get("state", "NO_SIGNAL"),
    })


# ==================== API - CSI ====================
@app.route('/api/csi/detect-hardware')
def api_csi_detect():
    """ESP32 donanım tespiti."""
    return jsonify(csi.get_hardware_info())


@app.route('/api/csi/connect', methods=['POST'])
def api_csi_connect():
    """ESP32'ye bağlan (otomatik veya port belirterek)."""
    data = request.json or {}
    port = data.get('port')
    
    if port:
        result = csi.connect_port(port)
    else:
        result = csi.auto_connect()
    
    return jsonify(result)


@app.route('/api/csi/stream/start', methods=['POST'])
def api_csi_start():
    """CSI veri akışını başlat."""
    if not csi.reader.is_connected:
        # Otomatik bağlan
        connect_result = csi.auto_connect()
        if not connect_result.get("success"):
            return jsonify(connect_result)
    
    success = csi.start()
    return jsonify({"success": success, "mode": csi.mode})


@app.route('/api/csi/stream/stop', methods=['POST'])
def api_csi_stop():
    """CSI akışını durdur."""
    csi.stop()
    return jsonify({"success": True})


@app.route('/api/csi/status')
def api_csi_status():
    """CSI tam durum."""
    return jsonify(csi.get_current_state())


@app.route('/api/csi/disconnect', methods=['POST'])
def api_csi_disconnect():
    """ESP32 bağlantısını kes."""
    csi.disconnect()
    return jsonify({"success": True})


@app.route('/api/calibration/start', methods=['POST'])
def api_calibration_start():
    """Boş oda kalibrasyonu başlat."""
    result = csi.start_calibration()
    return jsonify(result)


@app.route('/api/calibration/finish', methods=['POST'])
def api_calibration_finish():
    """Kalibrasyonu bitir."""
    result = csi.finish_calibration()
    return jsonify(result)


# ==================== API - AĞ TARAMA (cihaz keşfi) ====================
@app.route('/api/scan/quick', methods=['POST'])
def api_quick_scan():
    """Hızlı ARP taraması - ağdaki cihazları bul."""
    devices = scanner.quick_scan()
    
    socketio.emit('scan_complete', {'devices': devices})
    
    return jsonify({
        "success": True,
        "devices": devices,
        "count": len(devices)
    })


@app.route('/api/scan/full', methods=['POST'])
def api_full_scan():
    """Detaylı tarama (port tarama dahil)."""
    devices = scanner.full_scan()
    
    socketio.emit('scan_complete', {'devices': devices})
    
    return jsonify({
        "success": True,
        "devices": devices,
        "count": len(devices)
    })


@app.route('/api/scan/auto/start', methods=['POST'])
def api_auto_scan_start():
    """Otomatik taramayı başlat."""
    interval = request.json.get('interval', 10) if request.json else 10
    scanner.start_continuous_scan(interval)
    return jsonify({"success": True, "interval": interval})


@app.route('/api/scan/auto/stop', methods=['POST'])
def api_auto_scan_stop():
    """Otomatik taramayı durdur."""
    scanner.stop_continuous_scan()
    return jsonify({"success": True})


# ==================== API - SİNYAL ANALİZİ ====================
@app.route('/api/analysis')
def api_analysis():
    """CSI analiz durumu."""
    return jsonify(csi.get_current_state())


# ==================== API - CİHAZLAR ====================
@app.route('/api/devices')
def api_devices():
    """Tespit edilen tüm cihazları getir."""
    return jsonify({
        "devices": scanner.devices,
        "count": len(scanner.devices)
    })


@app.route('/api/devices/humans')
def api_humans():
    """İnsan taşıdığı tespit edilen cihazları getir."""
    humans = scanner.get_humans()
    return jsonify({
        "humans": humans,
        "count": len(humans)
    })


@app.route('/api/device/<ip>')
def api_device_detail(ip):
    """Belirli bir cihazın detaylarını getir."""
    device = scanner.get_device_by_ip(ip)
    if device:
        mac = device.get("mac", "")
        position = locator.device_positions.get(mac, {})
        movement = locator.get_movement_analysis(mac)
        return jsonify({
            "device": device,
            "position": position,
            "movement": movement
        })
    return jsonify({"error": "Cihaz bulunamadı"}), 404


@app.route('/api/device/ping/<ip>')
def api_ping_device(ip):
    """Cihazı pingleyerek erişilebilirlik kontrolü."""
    result = messenger.ping_device(ip)
    return jsonify(result)


# ==================== API - KONUM ====================
@app.route('/api/positions')
def api_positions():
    """Tüm cihaz konumlarını getir."""
    positions = locator.estimate_all_positions(scanner.devices)
    return jsonify({
        "positions": positions,
        "count": len(positions)
    })


@app.route('/api/positions/heatmap')
def api_heatmap():
    """Isı haritası verisini getir."""
    heatmap = locator.get_heatmap_data()
    return jsonify({"heatmap": heatmap})


@app.route('/api/positions/floor')
def api_floor_positions():
    """Kat planı pozisyonlarını getir."""
    positions = locator.get_floor_plan_positions()
    return jsonify({"positions": positions})


# ==================== API - MESAJLAŞMA ====================
@app.route('/api/message/send', methods=['POST'])
def api_send_message():
    """Mesaj gönder."""
    data = request.json
    if not data:
        return jsonify({"error": "Geçersiz istek"}), 400
    
    target_ip = data.get('target_ip', '')
    message = data.get('message', '')
    sender_name = data.get('sender_name', '')
    method = data.get('method', 'udp')  # udp, tcp, broadcast
    
    if not message:
        return jsonify({"error": "Mesaj boş olamaz"}), 400
    
    if target_ip == 'broadcast' or not target_ip:
        result = messenger.broadcast_message(message, sender_name)
    elif method == 'tcp':
        result = messenger.send_tcp_message(target_ip, message, sender_name)
    else:
        result = messenger.send_udp_message(target_ip, message, sender_name)
    
    return jsonify(result)


@app.route('/api/message/broadcast', methods=['POST'])
def api_broadcast_message():
    """Tüm ağa broadcast mesaj gönder."""
    data = request.json
    if not data:
        return jsonify({"error": "Geçersiz istek"}), 400
    
    message = data.get('message', '')
    sender_name = data.get('sender_name', '')
    
    if not message:
        return jsonify({"error": "Mesaj boş olamaz"}), 400
    
    result = messenger.broadcast_message(message, sender_name)
    return jsonify(result)


@app.route('/api/message/notify', methods=['POST'])
def api_send_notification():
    """Bildirim gönder."""
    data = request.json
    if not data:
        return jsonify({"error": "Geçersiz istek"}), 400
    
    target_ip = data.get('target_ip', '')
    title = data.get('title', 'WiFi Radar')
    message = data.get('message', '')
    sender_name = data.get('sender_name', '')
    
    if not target_ip or not message:
        return jsonify({"error": "Hedef IP ve mesaj gerekli"}), 400
    
    result = messenger.send_notification(target_ip, title, message, sender_name)
    return jsonify(result)


@app.route('/api/messages')
def api_messages():
    """Mesaj geçmişini getir."""
    sent = messenger.get_sent_messages()
    received = messenger.get_received_messages()
    return jsonify({
        "sent": sent,
        "received": received
    })


@app.route('/api/messages/conversation/<ip>')
def api_conversation(ip):
    """Belirli bir cihaz ile mesajlaşma geçmişi."""
    conversation = messenger.get_conversation(ip)
    return jsonify({"conversation": conversation})


# ==================== CHAT - ANLIK MESAJLAŞMA ====================
chat_users = {}  # sid -> {id, name}


@app.route('/chat')
def chat_page():
    """Mobil uyumlu sohbet sayfası - ağdaki herkes bağlanabilir."""
    return render_template('chat.html')


@socketio.on('chat_join')
def handle_chat_join(data):
    """Kullanıcı sohbete katıldı."""
    from flask_socketio import join_room
    from flask import request as flask_request
    
    user_id = data.get('id', '')
    user_name = data.get('name', 'Anonim')
    sid = getattr(flask_request, 'sid', None)
    
    chat_users[sid] = {'id': user_id, 'name': user_name}
    join_room('chat_room')
    
    # Herkese bildir
    emit('chat_system', {
        'message': f'{user_name} sohbete katıldı'
    }, room='chat_room')
    
    # Online sayısı güncelle
    socketio.emit('online_update', {'count': len(chat_users)}, room='chat_room')


@socketio.on('chat_send')
def handle_chat_send(data):
    """Sohbet mesajı gönderildi - herkese yayınla."""
    from datetime import datetime
    
    message = data.get('message', '').strip()
    sender_id = data.get('sender_id', '')
    sender_name = data.get('sender_name', 'Anonim')
    
    if not message:
        return
    
    # Herkese gönder
    socketio.emit('chat_message', {
        'sender_id': sender_id,
        'sender_name': sender_name,
        'message': message,
        'timestamp': datetime.now().isoformat()
    }, room='chat_room')


@socketio.on('disconnect')
def handle_disconnect():
    """Kullanıcı ayrıldı."""
    from flask import request as flask_request
    sid = getattr(flask_request, 'sid', None)
    
    user = chat_users.pop(sid, None)
    if user:
        socketio.emit('chat_system', {
            'message': f'{user["name"]} ayrıldı'
        }, room='chat_room')
        socketio.emit('online_update', {'count': len(chat_users)}, room='chat_room')


# Admin panelden mesaj gönder (belirli cihaza veya herkese)
@app.route('/api/chat/broadcast', methods=['POST'])
def api_chat_broadcast():
    """Radar panelinden chat kullanıcılarına mesaj gönder (hedefli veya herkese)."""
    data = request.json
    if not data:
        return jsonify({"error": "Geçersiz istek"}), 400
    
    message = data.get('message', '')
    target_ip = data.get('target_ip', '')
    if not message:
        return jsonify({"error": "Mesaj boş olamaz"}), 400
    
    from datetime import datetime
    
    # Hedefli veya broadcast mesaj - chat'e bağlı herkese gider
    # Hedef IP bilgisi ile birlikte gönderilir (client tarafında filtreleme yapılabilir)
    socketio.emit('admin_broadcast', {
        'message': message,
        'target_ip': target_ip,
        'timestamp': datetime.now().isoformat()
    }, room='chat_room')
    
    return jsonify({"success": True, "recipients": len(chat_users)})


# ==================== SOCKET.IO OLAYLARI ====================
@socketio.on('connect')
def handle_connect():
    """İstemci bağlandığında."""
    from flask_socketio import join_room
    join_room('chat_room')
    emit('scan_update', {
        'devices': scanner.devices,
        'positions': locator.device_positions
    })


@socketio.on('request_scan')
def handle_scan_request(data):
    """İstemciden tarama isteği."""
    scan_type = data.get('type', 'quick') if data else 'quick'
    
    if scan_type == 'full':
        devices = scanner.full_scan()
    else:
        devices = scanner.quick_scan()
    
    positions = locator.estimate_all_positions(devices)
    
    emit('scan_complete', {
        'devices': devices,
        'positions': positions
    }, broadcast=True)


@socketio.on('send_message')
def handle_send_message(data):
    """Socket üzerinden mesaj gönderme."""
    target_ip = data.get('target_ip', '')
    message = data.get('message', '')
    sender_name = data.get('sender_name', '')
    
    if target_ip == 'broadcast':
        result = messenger.broadcast_message(message, sender_name)
    else:
        result = messenger.send_udp_message(target_ip, message, sender_name)
    
    emit('message_sent', result)


# ==================== MESAJ CALLBACK ====================
def on_message_received(message_data):
    """Mesaj alındığında Socket.IO ile bildiri."""
    socketio.emit('new_message', message_data)


# Messenger callback ayarla
messenger.set_message_callback(on_message_received)


# ==================== UYGULAMA BAŞLATMA ====================
def start_app(host='0.0.0.0', port=5000, debug=False):
    """Uygulamayı başlat."""
    print(f"""
    ╔══════════════════════════════════════════════╗
    ║          WiFi RADAR v1.0                     ║
    ║   İnsan & Cihaz Tespit Sistemi               ║
    ╠══════════════════════════════════════════════╣
    ║  Sunucu: http://{host}:{port}               ║
    ║  Yerel:  http://localhost:{port}             ║
    ║  Ağ IP:  {scanner.local_ip}                  ║
    ╠══════════════════════════════════════════════╣
    ║  Mesaj Dinleme: Port {messenger.listen_port}            ║
    ║  Broadcast:     Port {messenger.broadcast_port}            ║
    ╚══════════════════════════════════════════════╝
    """)
    
    # Mesaj dinleyiciyi başlat
    messenger.start_listening()
    
    try:
        socketio.run(app, host=host, port=port, debug=debug,
                     allow_unsafe_werkzeug=True)
    finally:
        messenger.stop_listening()
        scanner.stop_continuous_scan()


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='WiFi Radar - İnsan & Cihaz Tespit Sistemi')
    parser.add_argument('--host', default='0.0.0.0', help='Dinlenecek adres (varsayılan: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=5000, help='Port numarası (varsayılan: 5000)')
    parser.add_argument('--debug', action='store_true', help='Debug modu')
    
    args = parser.parse_args()
    start_app(host=args.host, port=args.port, debug=args.debug)
