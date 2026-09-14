"""
WiFi Radar - Mesaj Gönderme Modülü
Aynı ağdaki cihazlara çeşitli yöntemlerle mesaj gönderir.
"""

import socket
import threading
import json
import time
from datetime import datetime
from typing import Dict, List, Optional, Callable


class NetworkMessenger:
    """
    Ağdaki cihazlara mesaj gönderme ve alma işlemleri.
    
    Yöntemler:
    1. UDP Broadcast - Tüm ağa mesaj yayınla
    2. UDP Unicast - Belirli bir cihaza mesaj gönder
    3. TCP Direct - Doğrudan bağlantı ile mesaj gönder
    4. Bildirim Servisi - Alıcı tarafta popup/bildirim göster
    """

    DEFAULT_PORT = 9876  # WiFi Radar mesajlaşma portu
    BROADCAST_PORT = 9877  # Broadcast mesaj portu
    BUFFER_SIZE = 4096
    PROTOCOL_VERSION = "1.0"

    def __init__(self, listen_port: int = None):
        self.listen_port = listen_port or self.DEFAULT_PORT
        self.broadcast_port = self.BROADCAST_PORT
        self.messages_sent: List[dict] = []
        self.messages_received: List[dict] = []
        self.is_listening = False
        self._listener_thread: Optional[threading.Thread] = None
        self._broadcast_listener_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._on_message_callback: Optional[Callable] = None
        self.local_ip = self._get_local_ip()
        self.device_name = socket.gethostname()

    def _get_local_ip(self) -> str:
        """Yerel IP adresini al."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def set_message_callback(self, callback: Callable):
        """Mesaj alındığında çağrılacak fonksiyonu ayarla."""
        self._on_message_callback = callback

    def _create_message_packet(self, message: str, msg_type: str = "text",
                                target_ip: str = "", sender_name: str = "") -> bytes:
        """Mesaj paketi oluştur."""
        packet = {
            "protocol": "wifi_radar",
            "version": self.PROTOCOL_VERSION,
            "type": msg_type,
            "sender_ip": self.local_ip,
            "sender_name": sender_name or self.device_name,
            "target_ip": target_ip,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }
        return json.dumps(packet).encode("utf-8")

    def _parse_message_packet(self, data: bytes) -> Optional[dict]:
        """Mesaj paketini parse et."""
        try:
            packet = json.loads(data.decode("utf-8"))
            if packet.get("protocol") == "wifi_radar":
                return packet
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        return None

    # ==================== MESAJ GÖNDERME ====================

    def send_udp_message(self, target_ip: str, message: str,
                          sender_name: str = "") -> dict:
        """
        UDP ile belirli bir cihaza mesaj gönder.
        
        Args:
            target_ip: Hedef IP adresi
            message: Gönderilecek mesaj
            sender_name: Gönderen adı
            
        Returns:
            Gönderim sonucu
        """
        result = {
            "success": False,
            "target_ip": target_ip,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "method": "udp_unicast"
        }

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(5)
            packet = self._create_message_packet(
                message, "text", target_ip, sender_name
            )
            sock.sendto(packet, (target_ip, self.listen_port))
            sock.close()

            result["success"] = True
            self.messages_sent.append(result)

        except socket.timeout:
            result["error"] = "Zaman aşımı - cihaz yanıt vermedi"
        except ConnectionRefusedError:
            result["error"] = "Bağlantı reddedildi - hedef port kapalı"
        except OSError as e:
            result["error"] = f"Ağ hatası: {str(e)}"

        return result

    def send_tcp_message(self, target_ip: str, message: str,
                          sender_name: str = "") -> dict:
        """
        TCP ile belirli bir cihaza mesaj gönder (güvenilir teslimat).
        
        Args:
            target_ip: Hedef IP adresi
            message: Gönderilecek mesaj
            sender_name: Gönderen adı
            
        Returns:
            Gönderim sonucu
        """
        result = {
            "success": False,
            "target_ip": target_ip,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "method": "tcp_direct"
        }

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((target_ip, self.listen_port))

            packet = self._create_message_packet(
                message, "text", target_ip, sender_name
            )
            sock.sendall(packet)

            # Onay bekle
            response = sock.recv(self.BUFFER_SIZE)
            if response:
                ack = self._parse_message_packet(response)
                if ack and ack.get("type") == "ack":
                    result["acknowledged"] = True

            sock.close()
            result["success"] = True
            self.messages_sent.append(result)

        except socket.timeout:
            result["error"] = "Zaman aşımı - cihaz yanıt vermedi"
        except ConnectionRefusedError:
            result["error"] = "Bağlantı reddedildi - WiFi Radar çalışmıyor olabilir"
        except OSError as e:
            result["error"] = f"Ağ hatası: {str(e)}"

        return result

    def broadcast_message(self, message: str, sender_name: str = "") -> dict:
        """
        Tüm ağa broadcast mesaj gönder.
        
        Args:
            message: Gönderilecek mesaj
            sender_name: Gönderen adı
            
        Returns:
            Gönderim sonucu
        """
        result = {
            "success": False,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "method": "broadcast"
        }

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(2)

            packet = self._create_message_packet(
                message, "broadcast", "255.255.255.255", sender_name
            )
            sock.sendto(packet, ("255.255.255.255", self.broadcast_port))
            sock.close()

            result["success"] = True
            self.messages_sent.append(result)

        except OSError as e:
            result["error"] = f"Broadcast hatası: {str(e)}"

        return result

    def send_notification(self, target_ip: str, title: str, message: str,
                           sender_name: str = "") -> dict:
        """
        Hedef cihaza bildirim gönder.
        
        Args:
            target_ip: Hedef IP
            title: Bildirim başlığı
            message: Bildirim mesajı
            sender_name: Gönderen adı
            
        Returns:
            Gönderim sonucu
        """
        notification_msg = json.dumps({
            "title": title,
            "body": message,
            "sender": sender_name or self.device_name
        })

        result = {
            "success": False,
            "target_ip": target_ip,
            "title": title,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "method": "notification"
        }

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(5)
            packet = self._create_message_packet(
                notification_msg, "notification", target_ip, sender_name
            )
            sock.sendto(packet, (target_ip, self.listen_port))
            sock.close()

            result["success"] = True
            self.messages_sent.append(result)

        except OSError as e:
            result["error"] = f"Bildirim gönderme hatası: {str(e)}"

        return result

    # ==================== MESAJ ALMA ====================

    def start_listening(self):
        """Mesaj dinlemeyi başlat (UDP + TCP)."""
        if self.is_listening:
            return

        self._stop_event.clear()
        self.is_listening = True

        # UDP dinleyici
        self._listener_thread = threading.Thread(
            target=self._udp_listener_loop,
            daemon=True
        )
        self._listener_thread.start()

        # Broadcast dinleyici
        self._broadcast_listener_thread = threading.Thread(
            target=self._broadcast_listener_loop,
            daemon=True
        )
        self._broadcast_listener_thread.start()

        # TCP dinleyici
        self._tcp_listener_thread = threading.Thread(
            target=self._tcp_listener_loop,
            daemon=True
        )
        self._tcp_listener_thread.start()

    def _udp_listener_loop(self):
        """UDP mesaj dinleme döngüsü."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", self.listen_port))
            sock.settimeout(1)

            while not self._stop_event.is_set():
                try:
                    data, addr = sock.recvfrom(self.BUFFER_SIZE)
                    packet = self._parse_message_packet(data)
                    if packet and packet.get("sender_ip") != self.local_ip:
                        self._handle_received_message(packet, addr)
                except socket.timeout:
                    continue
                except Exception:
                    continue

            sock.close()
        except OSError:
            pass

    def _broadcast_listener_loop(self):
        """Broadcast mesaj dinleme döngüsü."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("0.0.0.0", self.broadcast_port))
            sock.settimeout(1)

            while not self._stop_event.is_set():
                try:
                    data, addr = sock.recvfrom(self.BUFFER_SIZE)
                    packet = self._parse_message_packet(data)
                    if packet and packet.get("sender_ip") != self.local_ip:
                        self._handle_received_message(packet, addr)
                except socket.timeout:
                    continue
                except Exception:
                    continue

            sock.close()
        except OSError:
            pass

    def _tcp_listener_loop(self):
        """TCP mesaj dinleme döngüsü."""
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", self.listen_port))
            server.listen(5)
            server.settimeout(1)

            while not self._stop_event.is_set():
                try:
                    client, addr = server.accept()
                    client.settimeout(5)
                    data = client.recv(self.BUFFER_SIZE)
                    packet = self._parse_message_packet(data)

                    if packet:
                        self._handle_received_message(packet, addr)
                        # ACK gönder
                        ack = self._create_message_packet("", "ack")
                        client.sendall(ack)

                    client.close()
                except socket.timeout:
                    continue
                except Exception:
                    continue

            server.close()
        except OSError:
            pass

    def _handle_received_message(self, packet: dict, addr: tuple):
        """Alınan mesajı işle."""
        message_record = {
            "sender_ip": packet.get("sender_ip", addr[0]),
            "sender_name": packet.get("sender_name", "Bilinmeyen"),
            "message": packet.get("message", ""),
            "type": packet.get("type", "text"),
            "timestamp": packet.get("timestamp", datetime.now().isoformat()),
            "received_at": datetime.now().isoformat()
        }

        self.messages_received.append(message_record)
        # Son 200 mesajı tut
        self.messages_received = self.messages_received[-200:]

        # Callback varsa çağır
        if self._on_message_callback:
            self._on_message_callback(message_record)

    def stop_listening(self):
        """Mesaj dinlemeyi durdur."""
        self._stop_event.set()
        self.is_listening = False
        if self._listener_thread:
            self._listener_thread.join(timeout=3)
        if self._broadcast_listener_thread:
            self._broadcast_listener_thread.join(timeout=3)

    # ==================== MESAJ GEÇMİŞİ ====================

    def get_sent_messages(self, limit: int = 50) -> List[dict]:
        """Gönderilen mesajları getir."""
        return self.messages_sent[-limit:]

    def get_received_messages(self, limit: int = 50) -> List[dict]:
        """Alınan mesajları getir."""
        return self.messages_received[-limit:]

    def get_conversation(self, ip: str) -> List[dict]:
        """Belirli bir IP ile olan mesajlaşma geçmişini getir."""
        conversation = []

        for msg in self.messages_sent:
            if msg.get("target_ip") == ip:
                conversation.append({**msg, "direction": "sent"})

        for msg in self.messages_received:
            if msg.get("sender_ip") == ip:
                conversation.append({**msg, "direction": "received"})

        # Zamana göre sırala
        conversation.sort(key=lambda x: x.get("timestamp", ""))
        return conversation

    def get_statistics(self) -> dict:
        """Mesajlaşma istatistikleri."""
        return {
            "total_sent": len(self.messages_sent),
            "total_received": len(self.messages_received),
            "is_listening": self.is_listening,
            "listen_port": self.listen_port,
            "broadcast_port": self.broadcast_port,
            "local_ip": self.local_ip,
            "device_name": self.device_name,
            "successful_sends": sum(
                1 for m in self.messages_sent if m.get("success")
            ),
            "failed_sends": sum(
                1 for m in self.messages_sent if not m.get("success")
            ),
        }

    def ping_device(self, target_ip: str) -> dict:
        """
        Hedef cihazın WiFi Radar mesaj servisinin aktif olup olmadığını kontrol et.
        
        Args:
            target_ip: Hedef IP adresi
            
        Returns:
            Ping sonucu
        """
        result = {
            "target_ip": target_ip,
            "reachable": False,
            "has_wifi_radar": False,
            "timestamp": datetime.now().isoformat()
        }

        try:
            # Önce genel erişilebilirlik kontrolü
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            connect_result = sock.connect_ex((target_ip, self.listen_port))

            if connect_result == 0:
                result["reachable"] = True
                # WiFi Radar protokol kontrolü
                ping_packet = self._create_message_packet("", "ping")
                sock.sendall(ping_packet)

                try:
                    response = sock.recv(self.BUFFER_SIZE)
                    pong = self._parse_message_packet(response)
                    if pong and pong.get("type") in ("ack", "pong"):
                        result["has_wifi_radar"] = True
                except socket.timeout:
                    pass

            sock.close()
        except OSError:
            pass

        return result
