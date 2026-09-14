"""
WiFi Radar - Sinyal Analiz Motoru
RSSI değişimlerini izleyerek hareket tespiti, engel/duvar tahmini ve ortam analizi yapar.
"""

import math
import time
import threading
import subprocess
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import deque


class SignalSample:
    """Tek bir sinyal ölçümü."""
    def __init__(self, rssi: int, timestamp: float = None):
        self.rssi = rssi
        self.timestamp = timestamp or time.time()


class DeviceSignalTracker:
    """Bir cihazın sinyal geçmişini takip eder."""
    
    def __init__(self, mac: str, max_history: int = 200):
        self.mac = mac
        self.samples: deque = deque(maxlen=max_history)
        self.baseline_rssi: Optional[float] = None
        self.is_moving = False
        self.movement_direction = "stable"  # approaching, moving_away, stable
        self.last_movement_time: Optional[float] = None
        self.presence_confidence = 0.0  # 0-1 arası insanın orada olma olasılığı
        
    def add_sample(self, rssi: int):
        """Yeni sinyal ölçümü ekle."""
        self.samples.append(SignalSample(rssi))
        self._update_baseline()
        self._detect_movement()
    
    def _update_baseline(self):
        """Ortam baseline RSSI'sini güncelle (sabit ortam sinyali)."""
        if len(self.samples) < 5:
            return
        # İlk 10 ölçümün ortalamasını baseline al
        if self.baseline_rssi is None and len(self.samples) >= 10:
            first_samples = list(self.samples)[:10]
            self.baseline_rssi = sum(s.rssi for s in first_samples) / len(first_samples)
    
    def _detect_movement(self):
        """RSSI değişimlerinden hareket tespit et."""
        if len(self.samples) < 5:
            self.is_moving = False
            return
        
        recent = list(self.samples)[-10:]
        rssi_values = [s.rssi for s in recent]
        
        # Varyans hesapla - yüksek varyans = hareket var
        mean = sum(rssi_values) / len(rssi_values)
        variance = sum((x - mean) ** 2 for x in rssi_values) / len(rssi_values)
        std_dev = math.sqrt(variance)
        
        # Hareket eşikleri
        # Normal ortamda RSSI varyansı 1-2 dBm
        # İnsan hareketi 3-8 dBm varyans oluşturur
        self.is_moving = std_dev > 2.5
        
        if self.is_moving:
            self.last_movement_time = time.time()
        
        # Yön analizi (son 5 vs önceki 5)
        if len(rssi_values) >= 6:
            older = rssi_values[:len(rssi_values)//2]
            newer = rssi_values[len(rssi_values)//2:]
            old_avg = sum(older) / len(older)
            new_avg = sum(newer) / len(newer)
            
            diff = new_avg - old_avg
            if diff > 2:
                self.movement_direction = "approaching"  # Sinyal güçleniyor = yaklaşıyor
            elif diff < -2:
                self.movement_direction = "moving_away"  # Sinyal zayıflıyor = uzaklaşıyor
            else:
                self.movement_direction = "stable"
        
        # İnsan varlığı güveni
        self._calculate_presence()
    
    def _calculate_presence(self):
        """İnsan varlığı olasılığını hesapla."""
        if len(self.samples) < 5:
            self.presence_confidence = 0.3
            return
        
        factors = []
        
        # 1. Hareket varyansı (insanlar küçük hareketler yapar)
        recent = list(self.samples)[-20:]
        rssi_values = [s.rssi for s in recent]
        variance = sum((x - sum(rssi_values)/len(rssi_values)) ** 2 for x in rssi_values) / len(rssi_values)
        
        if 1.5 < math.sqrt(variance) < 8:
            factors.append(0.7)  # İnsan hareketi paterni
        elif math.sqrt(variance) > 8:
            factors.append(0.3)  # Çok büyük değişim - muhtemelen yürüyüş
        else:
            factors.append(0.2)  # Çok sabit - muhtemelen sabit cihaz
        
        # 2. RSSI seviyesi (yakın = muhtemelen insan taşıyor)
        current_rssi = rssi_values[-1]
        if current_rssi > -50:
            factors.append(0.8)  # Çok yakın
        elif current_rssi > -65:
            factors.append(0.6)  # Yakın
        elif current_rssi > -75:
            factors.append(0.4)  # Orta
        else:
            factors.append(0.2)  # Uzak
        
        # 3. Periyodik hareket paterni (nefes alma, küçük kıpırdanma)
        if len(self.samples) >= 30:
            last_30 = [s.rssi for s in list(self.samples)[-30:]]
            # Basit periyodiklik kontrolü - sıfır geçiş sayısı
            mean_val = sum(last_30) / len(last_30)
            crossings = sum(1 for i in range(1, len(last_30)) 
                          if (last_30[i-1] - mean_val) * (last_30[i] - mean_val) < 0)
            if 4 < crossings < 15:
                factors.append(0.75)  # Periyodik = insan
            else:
                factors.append(0.3)
        
        self.presence_confidence = sum(factors) / len(factors) if factors else 0.3
    
    def get_current_rssi(self) -> int:
        """Güncel RSSI (son 3 ölçümün ortalaması)."""
        if not self.samples:
            return -100
        recent = list(self.samples)[-3:]
        return int(sum(s.rssi for s in recent) / len(recent))
    
    def get_signal_stability(self) -> float:
        """Sinyal kararlılığı (0=çok kararsız, 1=çok kararlı)."""
        if len(self.samples) < 5:
            return 0.5
        recent = [s.rssi for s in list(self.samples)[-10:]]
        variance = sum((x - sum(recent)/len(recent))**2 for x in recent) / len(recent)
        stability = max(0, 1 - (math.sqrt(variance) / 10))
        return round(stability, 2)
    
    def get_stats(self) -> dict:
        """Cihaz sinyal istatistikleri."""
        if not self.samples:
            return {}
        rssi_list = [s.rssi for s in self.samples]
        return {
            "current_rssi": self.get_current_rssi(),
            "min_rssi": min(rssi_list),
            "max_rssi": max(rssi_list),
            "avg_rssi": round(sum(rssi_list) / len(rssi_list), 1),
            "sample_count": len(rssi_list),
            "stability": self.get_signal_stability(),
            "is_moving": self.is_moving,
            "movement_direction": self.movement_direction,
            "presence_confidence": round(self.presence_confidence, 2),
            "baseline_rssi": round(self.baseline_rssi, 1) if self.baseline_rssi else None,
        }


class WifiSignalAnalyzer:
    """
    Ana sinyal analiz motoru.
    Tüm cihazların sinyallerini takip eder, ortam haritası oluşturur.
    """
    
    # Ortam sabitleri
    WALL_ATTENUATION = 6  # dBm - bir duvarın yaklaşık sinyal zayıflatması
    HUMAN_BODY_ATTENUATION = 3  # dBm - insan vücudunun sinyal zayıflatması
    FREE_SPACE_LOSS_EXP = 2.0  # Açık alan kayıp üssü
    INDOOR_LOSS_EXP = 3.0  # Kapalı alan kayıp üssü
    
    def __init__(self):
        self.trackers: Dict[str, DeviceSignalTracker] = {}
        self.environment_map: Dict[str, dict] = {}  # grid hücreleri
        self.scan_history: List[dict] = []
        self.wall_segments: List[dict] = []  # Tespit edilen duvar/engeller
        self.is_analyzing = False
        self._analyze_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.grid_resolution = 20  # 20x20 grid
        self.area_size = 10.0  # 10m x 10m alan
        
        # Ortam baseline verileri
        self.environment_baseline: Dict[str, float] = {}
        self.obstruction_map = [[0.0] * self.grid_resolution for _ in range(self.grid_resolution)]
        self.signal_heatmap = [[0.0] * self.grid_resolution for _ in range(self.grid_resolution)]
        self.movement_heatmap = [[0.0] * self.grid_resolution for _ in range(self.grid_resolution)]
    
    def process_device(self, mac: str, rssi: int, ip: str = "", 
                       vendor: str = "", device_type: str = ""):
        """Cihaz sinyal verisini işle."""
        if mac not in self.trackers:
            self.trackers[mac] = DeviceSignalTracker(mac)
        
        self.trackers[mac].add_sample(rssi)
    
    def process_scan_results(self, devices: Dict[str, dict]):
        """Tarama sonuçlarını toplu işle."""
        for mac, device in devices.items():
            rssi = device.get("rssi", -70)
            self.process_device(
                mac, rssi,
                ip=device.get("ip", ""),
                vendor=device.get("vendor", ""),
                device_type=device.get("device_type", "")
            )
        
        # Haritaları güncelle
        self._update_signal_heatmap(devices)
        self._update_obstruction_map(devices)
        self._update_movement_heatmap()
        self._detect_walls()
    
    def _update_signal_heatmap(self, devices: Dict[str, dict]):
        """Sinyal gücü heatmap'ini güncelle."""
        grid = self.grid_resolution
        cell_size = self.area_size / grid
        
        # Heatmap'i sıfırla (eski verilerle karıştır - yumuşak geçiş)
        for i in range(grid):
            for j in range(grid):
                self.signal_heatmap[i][j] *= 0.7  # Eski veriyi azalt
        
        for mac, device in devices.items():
            rssi = device.get("rssi", -70)
            distance = self._rssi_to_distance(rssi)
            angle = self._get_device_angle(mac, devices)
            
            # Cihazın grid pozisyonunu hesapla
            gx, gy = self._polar_to_grid(distance, angle)
            
            # Gaussian yayılım - cihaz etrafına sinyal gücü ekle
            for i in range(grid):
                for j in range(grid):
                    dx = i - gx
                    dy = j - gy
                    dist_sq = dx*dx + dy*dy
                    # Sinyal gücü = yakınlığa göre gaussian
                    intensity = math.exp(-dist_sq / 8.0) * (1 - abs(rssi) / 100.0)
                    self.signal_heatmap[i][j] += intensity
        
        # Normalize et (0-1 arası)
        max_val = max(max(row) for row in self.signal_heatmap) or 1
        for i in range(grid):
            for j in range(grid):
                self.signal_heatmap[i][j] = min(1.0, self.signal_heatmap[i][j] / max_val)
    
    def _update_obstruction_map(self, devices: Dict[str, dict]):
        """Engel/duvar haritasını güncelle."""
        grid = self.grid_resolution
        
        for mac, device in devices.items():
            rssi = device.get("rssi", -70)
            distance = self._rssi_to_distance(rssi)
            angle = self._get_device_angle(mac, devices)
            
            # Beklenen RSSI vs gerçek RSSI farkı
            expected_rssi = self._expected_rssi_free_space(distance)
            rssi_deficit = expected_rssi - rssi  # Pozitif = sinyal beklenenden zayıf
            
            if rssi_deficit > 4:  # Önemli sinyal kaybı = engel var
                # Cihaz ile merkez arasındaki yolda engel var
                gx, gy = self._polar_to_grid(distance, angle)
                center = grid // 2
                
                # Çizgi boyunca engel işaretle
                steps = max(abs(gx - center), abs(gy - center)) or 1
                wall_distance_ratio = 0.3 + (rssi_deficit / 20.0) * 0.4  # Engel nerede?
                
                wx = int(center + (gx - center) * wall_distance_ratio)
                wy = int(center + (gy - center) * wall_distance_ratio)
                
                if 0 <= wx < grid and 0 <= wy < grid:
                    obstruction_strength = min(1.0, rssi_deficit / 15.0)
                    self.obstruction_map[wx][wy] = max(
                        self.obstruction_map[wx][wy],
                        obstruction_strength * 0.8 + self.obstruction_map[wx][wy] * 0.2
                    )
        
        # Zamanla azalt (dinamik engeller kaybolur)
        for i in range(grid):
            for j in range(grid):
                self.obstruction_map[i][j] *= 0.95
    
    def _update_movement_heatmap(self):
        """Hareket heatmap'ini güncelle - nerede hareket var?"""
        grid = self.grid_resolution
        
        # Önce azalt
        for i in range(grid):
            for j in range(grid):
                self.movement_heatmap[i][j] *= 0.8
        
        for mac, tracker in self.trackers.items():
            if tracker.is_moving:
                distance = self._rssi_to_distance(tracker.get_current_rssi())
                angle = self._get_tracker_angle(mac)
                gx, gy = self._polar_to_grid(distance, angle)
                
                # Hareket noktasına yoğunluk ekle
                for i in range(max(0, gx-2), min(grid, gx+3)):
                    for j in range(max(0, gy-2), min(grid, gy+3)):
                        dx = i - gx
                        dy = j - gy
                        intensity = math.exp(-(dx*dx + dy*dy) / 3.0)
                        self.movement_heatmap[i][j] += intensity * 0.5
        
        # Normalize
        max_val = max(max(row) for row in self.movement_heatmap) or 1
        if max_val > 1:
            for i in range(grid):
                for j in range(grid):
                    self.movement_heatmap[i][j] /= max_val
    
    def _detect_walls(self):
        """Engel haritasından duvar segmentleri çıkar."""
        grid = self.grid_resolution
        self.wall_segments = []
        threshold = 0.4
        
        for i in range(grid):
            for j in range(grid):
                if self.obstruction_map[i][j] > threshold:
                    # Bu noktada engel var
                    # Komşulara bak - bağlı engeller = duvar
                    segment = {
                        "x": i,
                        "y": j,
                        "strength": round(self.obstruction_map[i][j], 2),
                        "x_m": round((i / grid) * self.area_size - self.area_size/2, 2),
                        "y_m": round((j / grid) * self.area_size - self.area_size/2, 2),
                    }
                    self.wall_segments.append(segment)
    
    def _rssi_to_distance(self, rssi: int) -> float:
        """RSSI → metre."""
        if rssi >= 0:
            return 0.1
        ref_rssi = -40  # 1m referans
        n = 2.7  # Path loss exponent (kapalı alan)
        try:
            distance = 10 ** ((ref_rssi - rssi) / (10 * n))
            return max(0.1, min(distance, self.area_size))
        except:
            return 5.0
    
    def _expected_rssi_free_space(self, distance: float) -> float:
        """Açık alanda beklenen RSSI (engel olmadan)."""
        if distance <= 0:
            return -30
        ref_rssi = -40
        n = self.FREE_SPACE_LOSS_EXP
        return ref_rssi - 10 * n * math.log10(max(distance, 0.1))
    
    def _get_device_angle(self, mac: str, devices: Dict[str, dict]) -> float:
        """Cihazın açısını belirle (hash bazlı sabit açı)."""
        # MAC hash ile tutarlı açı ata
        hash_val = sum(ord(c) for c in mac)
        base_angle = (hash_val * 137.508) % 360  # Altın açı dağılımı
        return base_angle
    
    def _get_tracker_angle(self, mac: str) -> float:
        """Tracker'ın açısını getir."""
        hash_val = sum(ord(c) for c in mac)
        return (hash_val * 137.508) % 360
    
    def _polar_to_grid(self, distance: float, angle_deg: float) -> Tuple[int, int]:
        """Polar koordinatı grid hücresine çevir."""
        grid = self.grid_resolution
        center = grid // 2
        
        # Mesafeyi grid birimine çevir
        grid_dist = (distance / self.area_size) * grid
        
        angle_rad = math.radians(angle_deg)
        gx = int(center + grid_dist * math.cos(angle_rad))
        gy = int(center + grid_dist * math.sin(angle_rad))
        
        # Sınırla
        gx = max(0, min(grid - 1, gx))
        gy = max(0, min(grid - 1, gy))
        
        return gx, gy

    # ===== HAREKET & İNSAN TESPİTİ =====
    
    def get_detected_humans(self) -> List[dict]:
        """Sinyal analizine göre insan varlığı tespit edilen noktalar."""
        humans = []
        
        for mac, tracker in self.trackers.items():
            if tracker.presence_confidence > 0.5:
                distance = self._rssi_to_distance(tracker.get_current_rssi())
                angle = self._get_tracker_angle(mac)
                
                humans.append({
                    "mac": mac,
                    "confidence": tracker.presence_confidence,
                    "distance_m": round(distance, 2),
                    "angle": round(angle, 1),
                    "is_moving": tracker.is_moving,
                    "movement": tracker.movement_direction,
                    "rssi": tracker.get_current_rssi(),
                    "stability": tracker.get_signal_stability(),
                })
        
        return sorted(humans, key=lambda x: -x["confidence"])
    
    def get_movement_events(self) -> List[dict]:
        """Son hareket olayları."""
        events = []
        now = time.time()
        
        for mac, tracker in self.trackers.items():
            if tracker.is_moving:
                events.append({
                    "mac": mac,
                    "direction": tracker.movement_direction,
                    "rssi": tracker.get_current_rssi(),
                    "since": round(now - (tracker.last_movement_time or now), 1),
                })
        
        return events
    
    # ===== VERİ ÇIKIŞI (FRONTEND İÇİN) =====
    
    def get_full_analysis(self, devices: Dict[str, dict]) -> dict:
        """Frontend için tam analiz verisi."""
        self.process_scan_results(devices)
        
        grid = self.grid_resolution
        
        # Signal heatmap - frontend'e düz liste olarak
        signal_data = []
        for i in range(grid):
            for j in range(grid):
                val = self.signal_heatmap[i][j]
                if val > 0.05:
                    signal_data.append({
                        "x": i, "y": j,
                        "value": round(val, 3)
                    })
        
        # Engel haritası
        obstruction_data = []
        for i in range(grid):
            for j in range(grid):
                val = self.obstruction_map[i][j]
                if val > 0.2:
                    obstruction_data.append({
                        "x": i, "y": j,
                        "value": round(val, 3)
                    })
        
        # Hareket haritası
        movement_data = []
        for i in range(grid):
            for j in range(grid):
                val = self.movement_heatmap[i][j]
                if val > 0.1:
                    movement_data.append({
                        "x": i, "y": j,
                        "value": round(val, 3)
                    })
        
        # Cihaz pozisyonları (sinyal bazlı)
        device_positions = {}
        for mac, device in devices.items():
            rssi = device.get("rssi", -70)
            distance = self._rssi_to_distance(rssi)
            angle = self._get_device_angle(mac, devices)
            tracker = self.trackers.get(mac)
            
            device_positions[mac] = {
                "distance": round(distance, 2),
                "angle": round(angle, 1),
                "rssi": rssi,
                "is_human": tracker.presence_confidence > 0.5 if tracker else False,
                "presence_confidence": round(tracker.presence_confidence, 2) if tracker else 0,
                "is_moving": tracker.is_moving if tracker else False,
                "movement": tracker.movement_direction if tracker else "stable",
                "stability": tracker.get_signal_stability() if tracker else 0.5,
                "ip": device.get("ip", ""),
                "vendor": device.get("vendor", ""),
                "hostname": device.get("hostname", ""),
                "device_type": device.get("device_type", ""),
            }
        
        return {
            "signal_heatmap": signal_data,
            "obstruction_map": obstruction_data,
            "movement_heatmap": movement_data,
            "wall_segments": self.wall_segments,
            "device_positions": device_positions,
            "detected_humans": self.get_detected_humans(),
            "movement_events": self.get_movement_events(),
            "grid_size": grid,
            "area_size_m": self.area_size,
            "stats": {
                "total_tracked": len(self.trackers),
                "humans_detected": len([t for t in self.trackers.values() if t.presence_confidence > 0.5]),
                "moving_count": len([t for t in self.trackers.values() if t.is_moving]),
                "avg_confidence": round(
                    sum(t.presence_confidence for t in self.trackers.values()) / max(len(self.trackers), 1), 2
                ),
            }
        }
    
    def get_wifi_networks(self) -> List[dict]:
        """Çevredeki WiFi ağlarını tara (sinyal gücü analizi için)."""
        networks = []
        try:
            result = subprocess.run(
                ["netsh", "wlan", "show", "networks", "mode=Bssid"],
                capture_output=True, text=True, timeout=10, encoding='utf-8', errors='replace'
            )
            
            current = {}
            for line in result.stdout.split('\n'):
                line = line.strip()
                if line.startswith('SSID') and ':' in line and 'BSSID' not in line:
                    if current:
                        networks.append(current)
                    current = {"ssid": line.split(':', 1)[1].strip()}
                elif 'BSSID' in line and ':' in line:
                    current["bssid"] = line.split(':', 1)[1].strip()
                elif 'Sinyal' in line or 'Signal' in line:
                    match = re.search(r'(\d+)%', line)
                    if match:
                        percent = int(match.group(1))
                        # % -> dBm yaklaşık dönüşüm
                        current["signal_percent"] = percent
                        current["rssi"] = int((percent / 2) - 100)
                elif 'Kanal' in line or 'Channel' in line:
                    match = re.search(r'(\d+)', line.split(':')[1] if ':' in line else '')
                    if match:
                        current["channel"] = int(match.group(1))
            
            if current:
                networks.append(current)
                
        except Exception as e:
            pass
        
        return networks
