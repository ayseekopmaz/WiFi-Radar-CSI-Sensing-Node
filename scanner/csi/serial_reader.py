"""
ESP32 Serial Reader
COM port otomatik tespit, bağlantı yönetimi, veri okuma.
"""

import time
import threading
from typing import Optional, Callable, List
from collections import deque

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

from .parser import CSIParser, CSIFrame


class ESP32CSIReader:
    """
    ESP32'den serial port üzerinden CSI verisi okur.
    - Otomatik COM port tespiti
    - Bağlantı yönetimi
    - Thread-safe frame buffer
    - Callback desteği
    """
    
    BAUD_RATE = 115200
    ESP32_VID_PIDS = [
        (0x10C4, 0xEA60),   # CP2102 (yaygın ESP32 USB-Serial)
        (0x1A86, 0x7523),   # CH340
        (0x0403, 0x6001),   # FTDI FT232
        (0x303A, 0x1001),   # ESP32-S2/S3 native USB
        (0x1A86, 0x55D4),   # CH9102
    ]
    
    def __init__(self):
        self.port: Optional[str] = None
        self.serial_conn: Optional[object] = None
        self.parser = CSIParser()
        self.is_connected = False
        self.is_streaming = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Frame buffer (son 500 frame)
        self.frame_buffer: deque = deque(maxlen=500)
        self._buffer_lock = threading.Lock()
        
        # Callbacks
        self._on_frame: Optional[Callable] = None
        self._on_status: Optional[Callable] = None
        self._on_disconnect: Optional[Callable] = None
        
        # İstatistikler
        self.frames_received = 0
        self.last_frame_time = 0.0
        self.connect_time = 0.0
        self.esp32_info: dict = {}
        self.packets_per_second = 0.0
        self._pps_counter = 0
        self._pps_last_check = time.time()
    
    def set_callbacks(self, on_frame: Callable = None,
                      on_status: Callable = None,
                      on_disconnect: Callable = None):
        """Callback fonksiyonları ayarla."""
        self._on_frame = on_frame
        self._on_status = on_status
        self._on_disconnect = on_disconnect
    
    def detect_esp32(self) -> Optional[str]:
        """ESP32'nin bağlı olduğu COM portunu otomatik bul."""
        if not SERIAL_AVAILABLE:
            return None
        
        ports = serial.tools.list_ports.comports()
        
        for port in ports:
            # VID/PID kontrolü
            if port.vid and port.pid:
                for vid, pid in self.ESP32_VID_PIDS:
                    if port.vid == vid and port.pid == pid:
                        return port.device
            
            # Açıklama kontrolü
            desc = (port.description or "").lower()
            if any(k in desc for k in ["cp210", "ch340", "ch910", "ftdi", "esp32", "silicon labs"]):
                return port.device
        
        return None
    
    def list_ports(self) -> List[dict]:
        """Tüm serial portları listele."""
        if not SERIAL_AVAILABLE:
            return []
        
        result = []
        for port in serial.tools.list_ports.comports():
            result.append({
                "port": port.device,
                "description": port.description,
                "vid": port.vid,
                "pid": port.pid,
                "manufacturer": port.manufacturer,
                "is_esp32": self._is_esp32_port(port),
            })
        return result
    
    def _is_esp32_port(self, port) -> bool:
        """Port bir ESP32 mi?"""
        if port.vid and port.pid:
            for vid, pid in self.ESP32_VID_PIDS:
                if port.vid == vid and port.pid == pid:
                    return True
        desc = (port.description or "").lower()
        return any(k in desc for k in ["cp210", "ch340", "esp32"])
    
    def connect(self, port: str = None) -> bool:
        """ESP32'ye bağlan."""
        if not SERIAL_AVAILABLE:
            return False
        
        # Port belirtilmemişse otomatik bul
        if port is None:
            port = self.detect_esp32()
        
        if port is None:
            return False
        
        try:
            self.serial_conn = serial.Serial(
                port=port,
                baudrate=self.BAUD_RATE,
                timeout=1,
                write_timeout=1
            )
            self.port = port
            self.is_connected = True
            self.connect_time = time.time()
            
            # ESP32'nin boot mesajlarını bekle
            time.sleep(2)
            self._flush_boot()
            
            return True
            
        except (serial.SerialException, OSError) as e:
            self.is_connected = False
            return False
    
    def _flush_boot(self):
        """ESP32 boot mesajlarını temizle."""
        if self.serial_conn and self.serial_conn.in_waiting:
            try:
                while self.serial_conn.in_waiting:
                    line = self.serial_conn.readline().decode('utf-8', errors='ignore').strip()
                    info = self.parser.parse_info(line)
                    if info:
                        if info["type"] == "info":
                            self.esp32_info[info["data"][0]] = info["data"][1:]
            except:
                pass
    
    def start_stream(self) -> bool:
        """CSI veri akışını başlat (arka plan thread)."""
        if not self.is_connected:
            return False
        
        if self.is_streaming:
            return True
        
        self._stop_event.clear()
        self.is_streaming = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        return True
    
    def stop_stream(self):
        """Veri akışını durdur."""
        self._stop_event.set()
        self.is_streaming = False
        if self._thread:
            self._thread.join(timeout=3)
    
    def disconnect(self):
        """Bağlantıyı kapat."""
        self.stop_stream()
        if self.serial_conn:
            try:
                self.serial_conn.close()
            except:
                pass
        self.is_connected = False
        self.serial_conn = None
    
    def _read_loop(self):
        """Ana okuma döngüsü (thread içinde çalışır)."""
        while not self._stop_event.is_set():
            try:
                if not self.serial_conn or not self.serial_conn.is_open:
                    self.is_connected = False
                    if self._on_disconnect:
                        self._on_disconnect()
                    break
                
                if self.serial_conn.in_waiting:
                    line = self.serial_conn.readline().decode('utf-8', errors='ignore').strip()
                    
                    if not line:
                        continue
                    
                    # CSI frame mi?
                    if line.startswith("CSI,"):
                        frame = self.parser.parse_line(line)
                        if frame:
                            self.frames_received += 1
                            self.last_frame_time = time.time()
                            self._pps_counter += 1
                            
                            with self._buffer_lock:
                                self.frame_buffer.append(frame)
                            
                            if self._on_frame:
                                self._on_frame(frame)
                    
                    # Info/Status?
                    elif line.startswith(("INFO,", "STATUS,", "WARN,")):
                        info = self.parser.parse_info(line)
                        if info and self._on_status:
                            self._on_status(info)
                else:
                    time.sleep(0.005)  # 5ms bekle (CPU rahatlatma)
                
                # PPS hesapla
                now = time.time()
                if now - self._pps_last_check >= 1.0:
                    self.packets_per_second = self._pps_counter / (now - self._pps_last_check)
                    self._pps_counter = 0
                    self._pps_last_check = now
                    
            except (serial.SerialException, OSError):
                self.is_connected = False
                if self._on_disconnect:
                    self._on_disconnect()
                break
            except Exception:
                continue
    
    def get_latest_frames(self, count: int = 50) -> List[CSIFrame]:
        """Son N frame'i getir."""
        with self._buffer_lock:
            return list(self.frame_buffer)[-count:]
    
    def get_latest_frame(self) -> Optional[CSIFrame]:
        """Son frame."""
        with self._buffer_lock:
            return self.frame_buffer[-1] if self.frame_buffer else None
    
    def get_status(self) -> dict:
        """Bağlantı durumu."""
        return {
            "connected": self.is_connected,
            "streaming": self.is_streaming,
            "port": self.port,
            "frames_received": self.frames_received,
            "packets_per_second": round(self.packets_per_second, 1),
            "last_frame_age_s": round(time.time() - self.last_frame_time, 2) if self.last_frame_time else None,
            "uptime_s": round(time.time() - self.connect_time) if self.connect_time else 0,
            "buffer_size": len(self.frame_buffer),
            "parser_stats": self.parser.get_stats(),
            "esp32_info": self.esp32_info,
            "serial_available": SERIAL_AVAILABLE,
        }
