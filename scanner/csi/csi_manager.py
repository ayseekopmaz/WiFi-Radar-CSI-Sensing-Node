"""
CSI Manager - Tüm CSI bileşenlerini birleştirir.
Serial reader → Pipeline → Presence Engine → Web Output
"""

import time
import threading
from typing import Dict, Optional, Callable
from collections import deque

from .serial_reader import ESP32CSIReader
from .pipeline import CSIPipeline, ProcessedFrame
from .presence_engine import PresenceEngine, PresenceState
from .parser import CSIFrame


class CSIManager:
    """
    Ana CSI yönetici. ESP32'den veri okur, işler, durum çıkarır.
    Flask/SocketIO ile entegrasyon noktası.
    """
    
    MODE_DISCONNECTED = "DISCONNECTED"
    MODE_CSI = "CSI"
    MODE_SIMULATION = "SIMULATION"
    
    def __init__(self):
        self.reader = ESP32CSIReader()
        self.pipeline = CSIPipeline()
        self.engine = PresenceEngine()
        
        self.mode = self.MODE_DISCONNECTED
        self.is_running = False
        self._process_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Web'e gönderilecek son veriler
        self._state_lock = threading.Lock()
        self._last_state: Dict = {}
        self._amplitude_graph: deque = deque(maxlen=100)  # Son 100 frame amplitude ortalaması
        self._motion_graph: deque = deque(maxlen=200)
        self._energy_graph: deque = deque(maxlen=200)
        
        # Callback (SocketIO emit için)
        self._on_update: Optional[Callable] = None
        self._update_interval = 0.1  # 100ms'de bir web'e gönder
        self._last_emit = 0.0
    
    def set_update_callback(self, callback: Callable):
        """Web güncelleme callback'i."""
        self._on_update = callback
    
    def auto_connect(self) -> Dict:
        """ESP32'yi otomatik bul ve bağlan."""
        port = self.reader.detect_esp32()
        
        if port is None:
            return {
                "success": False,
                "error": "ESP32 bulunamadı. USB'ye takılı olduğundan emin ol.",
                "ports": self.reader.list_ports(),
                "mode": self.MODE_DISCONNECTED,
            }
        
        success = self.reader.connect(port)
        if success:
            self.mode = self.MODE_CSI
            return {
                "success": True,
                "port": port,
                "mode": self.MODE_CSI,
                "message": f"ESP32 bağlandı: {port}",
            }
        else:
            return {
                "success": False,
                "error": f"Port {port} açılamadı.",
                "mode": self.MODE_DISCONNECTED,
            }
    
    def connect_port(self, port: str) -> Dict:
        """Belirli bir porta bağlan."""
        success = self.reader.connect(port)
        if success:
            self.mode = self.MODE_CSI
            return {"success": True, "port": port, "mode": self.MODE_CSI}
        return {"success": False, "error": "Bağlantı başarısız"}
    
    def start(self) -> bool:
        """CSI toplama ve işlemeyi başlat."""
        if not self.reader.is_connected:
            return False
        
        self.reader.start_stream()
        self.is_running = True
        self._stop_event.clear()
        
        # İşleme thread'i
        self._process_thread = threading.Thread(
            target=self._process_loop, daemon=True)
        self._process_thread.start()
        
        return True
    
    def stop(self):
        """Durdur."""
        self._stop_event.set()
        self.is_running = False
        self.reader.stop_stream()
        if self._process_thread:
            self._process_thread.join(timeout=3)
    
    def disconnect(self):
        """Tamamen bağlantı kes."""
        self.stop()
        self.reader.disconnect()
        self.mode = self.MODE_DISCONNECTED
    
    def start_calibration(self) -> Dict:
        """Boş oda kalibrasyonu başlat."""
        self.pipeline.start_calibration()
        self.engine.start_calibration()
        return {"success": True, "message": "Kalibrasyon başladı. Odayı boş bırakın."}
    
    def finish_calibration(self) -> Dict:
        """Kalibrasyonu bitir."""
        success = self.pipeline.finish_calibration()
        if success:
            self.engine.finish_calibration()
            return {"success": True, "message": "Kalibrasyon tamamlandı.",
                    "samples": self.pipeline.background_samples}
        return {"success": False, "error": "Yetersiz veri. Daha fazla bekleyin."}
    
    def _process_loop(self):
        """Ana işleme döngüsü (thread)."""
        while not self._stop_event.is_set():
            # Yeni frame var mı?
            frame = self.reader.get_latest_frame()
            
            if frame:
                # Pipeline ile işle
                processed = self.pipeline.process(frame)
                
                if processed:
                    # Kalibrasyon modundaysa sample topla
                    if self.engine.state == PresenceState.CALIBRATING:
                        self.pipeline.add_calibration_sample(frame)
                    
                    # Durum güncelle
                    state = self.engine.update(processed)
                    
                    # Grafik verileri
                    self._amplitude_graph.append(processed.mean_amplitude)
                    self._motion_graph.append(processed.motion_score)
                    self._energy_graph.append(processed.total_energy)
                    
                    # Web'e gönder (throttled)
                    now = time.time()
                    if now - self._last_emit >= self._update_interval:
                        self._last_emit = now
                        self._emit_update(state, processed)
            else:
                # Timeout kontrolü
                self.engine.check_timeout()
            
            time.sleep(0.01)  # 10ms döngü
    
    def _emit_update(self, state: Dict, frame: ProcessedFrame):
        """Web'e durum gönder."""
        with self._state_lock:
            self._last_state = {
                "presence": state,
                "signal": {
                    "rssi": frame.rssi,
                    "snr": frame.snr,
                    "quality": frame.quality_score,
                    "motion_score": round(frame.motion_score, 3),
                    "energy": round(frame.total_energy, 4),
                    "amp_variance": round(frame.amplitude_variance, 4),
                    "phase_variance": round(frame.phase_variance, 4),
                    "correlation": round(frame.subcarrier_correlation, 3),
                    "num_subcarriers": frame.num_subcarriers,
                },
                "graphs": {
                    "amplitude": list(self._amplitude_graph)[-50:],
                    "motion": list(self._motion_graph)[-100:],
                    "energy": list(self._energy_graph)[-100:],
                },
                "system": {
                    "mode": self.mode,
                    "connected": self.reader.is_connected,
                    "streaming": self.reader.is_streaming,
                    "port": self.reader.port,
                    "pps": round(self.reader.packets_per_second, 1),
                    "frames_total": self.reader.frames_received,
                    "calibrated": self.pipeline.is_calibrated,
                    "pipeline_stats": self.pipeline.get_stats(),
                },
                "timestamp": time.time(),
            }
        
        if self._on_update:
            self._on_update(self._last_state)
    
    def get_current_state(self) -> Dict:
        """Son durum verisi (REST API için)."""
        with self._state_lock:
            if self._last_state:
                return self._last_state
        
        # Henüz veri yoksa
        return {
            "presence": self.engine.get_state(),
            "signal": None,
            "graphs": {"amplitude": [], "motion": [], "energy": []},
            "system": {
                "mode": self.mode,
                "connected": self.reader.is_connected,
                "streaming": self.reader.is_streaming,
                "port": self.reader.port,
                "pps": 0,
                "frames_total": 0,
                "calibrated": False,
                "pipeline_stats": self.pipeline.get_stats(),
            },
            "timestamp": time.time(),
        }
    
    def get_hardware_info(self) -> Dict:
        """Donanım bilgisi."""
        return {
            "serial_available": self.reader.is_connected or True,
            "ports": self.reader.list_ports(),
            "esp32_detected": self.reader.detect_esp32() is not None,
            "detected_port": self.reader.detect_esp32(),
            "connected": self.reader.is_connected,
            "current_port": self.reader.port,
            "mode": self.mode,
        }
