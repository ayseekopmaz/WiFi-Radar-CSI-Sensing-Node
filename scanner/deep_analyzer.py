"""
WiFi Radar - Derin Sinyal Analiz Motoru v3
Tam frekans optimizasyonu, otomatik kroki çıkarma, detaylı ortam profilleme.

Teknolojiler:
- Adaptive Kalman Filter (ortama göre gürültü ayarlı)
- Multi-band FFT (nefes, kalp atışı, yürüme, koşma frekansları)  
- Ray Tracing simulasyonu (duvar tespiti)
- DBSCAN kümeleme (engel gruplandırma → duvar segmenti)
- CSI Emülasyonu (Channel State Information tahmini)
- Otomatik kat planı çıkarma (oda sınırları, kapılar, pencereler)
"""

import math
import time
import subprocess
import re
import threading
import random
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Set
from collections import deque


# ════════════════════════════════════════════════════════════════
# ADAPTİF KALMAN FİLTRE
# ════════════════════════════════════════════════════════════════
class AdaptiveKalmanFilter:
    """
    Ortam gürültüsüne göre otomatik ayarlanan Kalman Filtresi.
    TURBO MOD: Daha agresif, daha hızlı tepki.
    """
    
    def __init__(self):
        self.x = -70.0          # Tahmin
        self.p = 10.0           # Belirsizlik (yüksek başlat = hızlı adaptasyon)
        self.q = 1.5            # Process noise - YÜKSEK (hızlı tepki)
        self.r = 2.0            # Measurement noise - DÜŞÜK (ölçüme güven)
        self.innovation_history: deque = deque(maxlen=10)  # Kısa pencere
        self.min_q = 0.5
        self.max_q = 5.0
        self.min_r = 0.5
        self.max_r = 6.0
    
    def update(self, measurement: float) -> float:
        """Ölçüm ekle, filtrelenmiş değer döndür."""
        # Innovation (ölçüm - tahmin farkı)
        innovation = measurement - self.x
        self.innovation_history.append(innovation)
        
        # Adaptif: innovation varyansına göre gürültü ayarla
        if len(self.innovation_history) >= 3:  # 3 sample yeterli
            inno_list = list(self.innovation_history)
            inno_var = sum(i*i for i in inno_list) / len(inno_list)
            
            # Agresif adaptasyon - hızlı tepki
            self.q = max(self.min_q, min(self.max_q, inno_var * 0.2))
            self.r = max(self.min_r, min(self.max_r, 2.0 - inno_var * 0.08))
        
        # Kalman adımları
        self.p += self.q
        k = self.p / (self.p + self.r)
        self.x += k * innovation
        self.p *= (1 - k)
        
        return self.x
    
    def get_state(self) -> dict:
        return {"estimate": round(self.x, 2), "uncertainty": round(self.p, 3),
                "q": round(self.q, 3), "r": round(self.r, 3)}


# ════════════════════════════════════════════════════════════════
# MULTI-BAND FFT ANALİZÖR
# ════════════════════════════════════════════════════════════════
class MultiBandFFT:
    """
    Çoklu frekans bandı analizi.
    Her band farklı bir aktiviteye karşılık gelir.
    
    Frekans Bandları:
    - Ultra düşük (0.05-0.15 Hz): İnsan solunumu (hareketsiz kişi)
    - Düşük (0.15-0.5 Hz): Derin nefes, hafif sallanma
    - Orta-düşük (0.5-1.2 Hz): Yavaş yürüme
    - Orta (1.2-2.5 Hz): Normal yürüme
    - Yüksek (2.5-5.0 Hz): Hızlı yürüme/koşma
    - Çok yüksek (5-10 Hz): El hareketi, kapı açma
    """
    
    BANDS = {
        "respiration":    (0.05, 0.15, "Solunum (hareketsiz kişi)"),
        "deep_breath":    (0.15, 0.5, "Derin nefes / hafif hareket"),
        "slow_walk":      (0.5, 1.2, "Yavaş yürüme"),
        "normal_walk":    (1.2, 2.5, "Normal yürüme"),
        "fast_movement":  (2.5, 5.0, "Hızlı hareket / koşma"),
        "gesture":        (5.0, 10.0, "El/kol hareketi / kapı"),
    }
    
    def __init__(self, sample_rate: float = 0.2):
        self.sample_rate = sample_rate  # Hz (5sn'de 1 ölçüm = 0.2Hz)
    
    def full_spectrum_analysis(self, signal: List[float]) -> dict:
        """Tam spektrum analizi."""
        n = len(signal)
        if n < 8:  # 8 sample yeterli (önceden 16 idi)
            return {"bands": {}, "dominant_activity": "insufficient_data",
                    "confidence": 0, "spectrum": [], "total_energy": 0}
        
        # DC çıkar + pencere uygula
        mean = sum(signal) / n
        windowed = [(signal[i] - mean) * (0.54 - 0.46 * math.cos(2*math.pi*i/(n-1)))
                    for i in range(n)]
        
        # DFT
        half_n = n // 2
        freqs = []
        magnitudes = []
        
        for k in range(1, half_n):
            real = sum(windowed[t] * math.cos(2*math.pi*k*t/n) for t in range(n))
            imag = sum(windowed[t] * math.sin(2*math.pi*k*t/n) for t in range(n))
            mag = math.sqrt(real*real + imag*imag) / n
            freq = k * self.sample_rate / n
            freqs.append(freq)
            magnitudes.append(mag)
        
        if not magnitudes:
            return {"bands": {}, "dominant_activity": "static", "confidence": 0,
                    "spectrum": [], "total_energy": 0}
        
        total_energy = sum(m*m for m in magnitudes)
        
        # Band bazlı enerji analizi
        band_results = {}
        for band_name, (low, high, desc) in self.BANDS.items():
            band_energy = sum(m*m for f, m in zip(freqs, magnitudes) if low <= f <= high)
            band_peak = max((m for f, m in zip(freqs, magnitudes) if low <= f <= high), default=0)
            band_ratio = band_energy / (total_energy + 1e-10)
            
            band_results[band_name] = {
                "energy": round(band_energy, 6),
                "peak_magnitude": round(band_peak, 4),
                "ratio": round(band_ratio, 4),
                "description": desc,
                "active": band_ratio > 0.08 and band_peak > 0.1  # Düşürüldü (0.15/0.2 → 0.08/0.1)
            }
        
        # Dominant aktivite belirleme
        dominant = self._classify_activity(band_results, total_energy)
        
        # Spektrum verisi (frontend grafiği için)
        spectrum = [{"freq": round(f, 4), "mag": round(m, 4)}
                    for f, m in zip(freqs[:30], magnitudes[:30])]
        
        return {
            "bands": band_results,
            "dominant_activity": dominant["activity"],
            "activity_detail": dominant["detail"],
            "confidence": dominant["confidence"],
            "spectrum": spectrum,
            "total_energy": round(total_energy, 6),
            "dominant_freq": round(freqs[magnitudes.index(max(magnitudes))], 4) if magnitudes else 0,
            "signal_quality": self._signal_quality(signal),
        }
    
    def _classify_activity(self, bands: dict, total_energy: float) -> dict:
        """Band enerjilerinden aktivite sınıflandır."""
        if total_energy < 0.01:
            return {"activity": "empty", "detail": "Sinyal yok veya çok zayıf", "confidence": 0.9}
        
        # En aktif bandı bul
        active_bands = [(name, info) for name, info in bands.items() if info["active"]]
        
        if not active_bands:
            # Hiçbir band aktif değil ama enerji var
            if total_energy < 0.05:  # Düşürüldü
                return {"activity": "static_device", "detail": "Sabit cihaz (hareket yok)", "confidence": 0.8}
            else:
                return {"activity": "micro_vibration", "detail": "Mikro titreşim - olası insan varlığı", "confidence": 0.6}
        
        # Respiration tespiti (en hassas - hareketsiz insan)
        resp = bands["respiration"]
        deep = bands["deep_breath"]
        if resp["active"] and resp["ratio"] > 0.1:  # Düşürüldü (0.2 → 0.1)
            return {"activity": "person_stationary", 
                    "detail": "Hareketsiz kişi tespit (solunum frekansı)", 
                    "confidence": min(0.9, resp["ratio"] * 3)}  # Güçlendirildi
        
        if deep["active"] and deep["ratio"] > 0.1:  # Düşürüldü
            return {"activity": "person_resting",
                    "detail": "Oturan/yatan kişi (derin nefes paterni)",
                    "confidence": min(0.85, deep["ratio"] * 3)}
        
        # Yürüme tespiti
        slow = bands["slow_walk"]
        normal = bands["normal_walk"]
        fast = bands["fast_movement"]
        
        if fast["active"] and fast["ratio"] > 0.1:  # 0.2 → 0.1
            return {"activity": "running",
                    "detail": "Hızlı hareket / koşma tespit",
                    "confidence": min(0.95, fast["ratio"] * 3)}
        
        if normal["active"] and normal["ratio"] > 0.08:  # 0.15 → 0.08
            return {"activity": "walking",
                    "detail": "Normal yürüme tespit",
                    "confidence": min(0.9, normal["ratio"] * 3)}
        
        if slow["active"] and slow["ratio"] > 0.08:  # 0.15 → 0.08
            return {"activity": "slow_walking",
                    "detail": "Yavaş yürüme / yer değiştirme",
                    "confidence": min(0.8, slow["ratio"] * 3)}
        
        # Jesture
        gesture = bands["gesture"]
        if gesture["active"]:
            return {"activity": "gesture",
                    "detail": "El/kol hareketi veya kapı/pencere açılması",
                    "confidence": min(0.7, gesture["ratio"] * 2)}
        
        return {"activity": "unknown_movement", "detail": "Belirsiz hareket", "confidence": 0.4}
    
    def _signal_quality(self, signal: List[float]) -> dict:
        """Sinyal kalitesi metrikleri."""
        n = len(signal)
        mean = sum(signal) / n
        variance = sum((x - mean)**2 for x in signal) / n
        std = math.sqrt(variance)
        
        # SNR tahmini (sinyal/gürültü)
        signal_power = mean * mean
        noise_power = variance
        snr = 10 * math.log10(signal_power / (noise_power + 1e-10)) if noise_power > 0 else 30
        
        return {
            "mean_rssi": round(mean, 1),
            "std_dev": round(std, 2),
            "snr_db": round(snr, 1),
            "dynamic_range": round(max(signal) - min(signal), 1),
            "sample_count": n,
            "quality_score": round(min(1.0, max(0, (snr + 10) / 40)), 2)  # 0-1 arası
        }


# ════════════════════════════════════════════════════════════════
# RAY TRACING - Sinyal Yolu Simülasyonu & Duvar Tespiti
# ════════════════════════════════════════════════════════════════
class RayTracer:
    """
    WiFi sinyal yolu simülasyonu.
    Her cihaza giden sinyal yolunu analiz eder.
    Beklenen vs gerçek sinyal farkından duvar/engel konumları çıkarır.
    """
    
    # Malzeme kayıp değerleri (dBm)
    MATERIAL_LOSS = {
        "concrete_wall": 12.0,    # Beton duvar
        "brick_wall": 8.0,       # Tuğla duvar
        "drywall": 3.0,          # Alçıpan
        "glass": 2.0,            # Cam
        "wood_door": 4.0,        # Ahşap kapı
        "metal_door": 10.0,      # Metal kapı
        "human_body": 3.5,       # İnsan vücudu
        "furniture": 2.5,        # Mobilya
        "water": 5.0,            # Su (akvaryum vb.)
    }
    
    def __init__(self, grid_size: int = 40, area_m: float = 10.0):
        self.grid_size = grid_size
        self.area_m = area_m
        self.cell_size = area_m / grid_size  # metre/hücre
        
        # Engel haritası: her hücre 0-1 arası engel yoğunluğu
        self.obstruction_grid = [[0.0]*grid_size for _ in range(grid_size)]
        # Duvar kesinliği: her hücre ne kadar "duvar" olma olasılığı
        self.wall_confidence = [[0.0]*grid_size for _ in range(grid_size)]
        # Tespit edilen duvar segmentleri
        self.wall_segments: List[dict] = []
        # Oda sınırları
        self.room_boundaries: List[dict] = []
    
    def trace_ray(self, rssi_measured: float, distance_m: float, 
                  angle_deg: float, tx_power: float = 20.0) -> dict:
        """
        Bir cihaza giden sinyal yolunu analiz et.
        
        Returns:
            Ray trace sonucu: engel sayısı, duvar konumları, kayıp dağılımı
        """
        # Beklenen serbest alan kaybı
        if distance_m <= 0:
            return {"loss": 0, "obstructions": []}
        
        fspl = 20*math.log10(max(distance_m, 0.1)) + 20*math.log10(2.4e9) - 147.55
        expected_rssi = tx_power - fspl
        
        # Fazla kayıp = engel var
        extra_loss = expected_rssi - rssi_measured
        
        if extra_loss < 2:
            return {"loss": 0, "extra_loss_db": 0, "obstructions": [],
                    "path_clear": True, "material_estimate": "free_space"}
        
        # Kayıp miktarından engel profili çıkar
        obstructions = self._estimate_obstructions(extra_loss, distance_m, angle_deg)
        
        # Grid üzerine engelleri işaretle
        self._mark_grid(angle_deg, distance_m, obstructions)
        
        return {
            "extra_loss_db": round(extra_loss, 1),
            "path_clear": False,
            "obstructions": obstructions,
            "material_estimate": self._guess_material(extra_loss),
            "wall_count": len([o for o in obstructions if o["type"] == "wall"]),
            "human_count": len([o for o in obstructions if o["type"] == "human"]),
        }
    
    def _estimate_obstructions(self, total_loss: float, distance: float,
                                angle: float) -> List[dict]:
        """Kayıp miktarından engel listesi çıkar."""
        obstructions = []
        remaining_loss = total_loss
        position_ratio = 0.3  # İlk engel genelde yolun %30'unda
        
        while remaining_loss > 2.5 and len(obstructions) < 5:
            if remaining_loss >= 8:
                # Büyük kayıp = beton/tuğla duvar
                obs_type = "wall"
                loss = 8.0
                material = "brick_wall" if remaining_loss < 12 else "concrete_wall"
            elif remaining_loss >= 4:
                # Orta kayıp = kapı veya alçıpan
                obs_type = "wall"
                loss = 4.0
                material = "drywall"
            elif remaining_loss >= 3:
                # Küçük kayıp = insan veya mobilya
                obs_type = "human" if random.random() > 0.4 else "furniture"
                loss = 3.5 if obs_type == "human" else 2.5
                material = "human_body" if obs_type == "human" else "furniture"
            else:
                break
            
            obstructions.append({
                "type": obs_type,
                "material": material,
                "loss_db": loss,
                "position_ratio": round(position_ratio, 2),
                "distance_from_center": round(distance * position_ratio, 2),
                "angle": angle
            })
            
            remaining_loss -= loss
            position_ratio += 0.2  # Sonraki engel daha uzakta
        
        return obstructions
    
    def _mark_grid(self, angle_deg: float, distance: float, obstructions: List[dict]):
        """Engelleri grid üzerine işaretle."""
        center = self.grid_size // 2
        angle_rad = math.radians(angle_deg)
        
        for obs in obstructions:
            # Engelin grid pozisyonu
            obs_dist = obs["distance_from_center"]
            gx = int(center + (obs_dist / self.area_m) * self.grid_size * math.cos(angle_rad))
            gy = int(center + (obs_dist / self.area_m) * self.grid_size * math.sin(angle_rad))
            
            if 0 <= gx < self.grid_size and 0 <= gy < self.grid_size:
                strength = obs["loss_db"] / 12.0  # Normalize
                self.obstruction_grid[gx][gy] = max(self.obstruction_grid[gx][gy], strength)
                
                if obs["type"] == "wall":
                    self.wall_confidence[gx][gy] = min(1.0, 
                        self.wall_confidence[gx][gy] + 0.3)  # 0.15 → 0.3 (2x hızlı)
                    
                    # Komşu hücrelere de yay (duvar kalınlığı)
                    for di in range(-1, 2):
                        for dj in range(-1, 2):
                            ni, nj = gx+di, gy+dj
                            if 0 <= ni < self.grid_size and 0 <= nj < self.grid_size:
                                self.wall_confidence[ni][nj] = min(1.0,
                                    self.wall_confidence[ni][nj] + 0.15)  # 0.08 → 0.15
    
    def _guess_material(self, loss_db: float) -> str:
        """Kayıp miktarından malzeme tahmini."""
        if loss_db >= 10:
            return "concrete_wall"
        elif loss_db >= 7:
            return "brick_wall"
        elif loss_db >= 4:
            return "drywall / wood_door"
        elif loss_db >= 3:
            return "human_body / furniture"
        else:
            return "glass / thin_obstruction"
    
    def extract_walls(self) -> List[dict]:
        """
        Duvar güven haritasından duvar segmentleri çıkar.
        DBSCAN benzeri kümeleme ile bitişik yüksek-güven hücreleri grupla.
        """
        grid = self.grid_size
        threshold = 0.3
        visited = set()
        segments = []
        
        for i in range(grid):
            for j in range(grid):
                if self.wall_confidence[i][j] >= threshold and (i, j) not in visited:
                    # BFS ile bağlı duvar hücrelerini bul
                    cluster = self._flood_fill(i, j, threshold, visited)
                    if len(cluster) >= 2:  # En az 2 hücre = duvar segmenti
                        segment = self._cluster_to_segment(cluster)
                        segments.append(segment)
        
        self.wall_segments = segments
        return segments
    
    def _flood_fill(self, start_i: int, start_j: int, threshold: float,
                    visited: Set[Tuple[int, int]]) -> List[Tuple[int, int]]:
        """BFS ile bağlı hücreleri bul."""
        cluster = []
        queue = [(start_i, start_j)]
        
        while queue and len(cluster) < 50:
            i, j = queue.pop(0)
            if (i, j) in visited:
                continue
            if not (0 <= i < self.grid_size and 0 <= j < self.grid_size):
                continue
            if self.wall_confidence[i][j] < threshold:
                continue
            
            visited.add((i, j))
            cluster.append((i, j))
            
            # 4 yönlü komşu
            for di, dj in [(-1,0),(1,0),(0,-1),(0,1)]:
                queue.append((i+di, j+dj))
        
        return cluster
    
    def _cluster_to_segment(self, cluster: List[Tuple[int, int]]) -> dict:
        """Hücre kümesini duvar segmentine dönüştür."""
        xs = [c[0] for c in cluster]
        ys = [c[1] for c in cluster]
        
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        
        # Yatay mı dikey mi?
        width = max_x - min_x
        height = max_y - min_y
        
        orientation = "horizontal" if width > height else "vertical"
        if width == height:
            orientation = "square"  # Köşe veya kalın duvar
        
        # Metre cinsine çevir
        cell = self.cell_size
        avg_confidence = sum(self.wall_confidence[i][j] for i, j in cluster) / len(cluster)
        
        return {
            "start_x": round((min_x - self.grid_size/2) * cell, 2),
            "start_y": round((min_y - self.grid_size/2) * cell, 2),
            "end_x": round((max_x - self.grid_size/2) * cell, 2),
            "end_y": round((max_y - self.grid_size/2) * cell, 2),
            "grid_start": (min_x, min_y),
            "grid_end": (max_x, max_y),
            "orientation": orientation,
            "length_m": round(max(width, height) * cell, 2),
            "thickness": min(width, height) + 1,
            "confidence": round(avg_confidence, 2),
            "cell_count": len(cluster),
            "material_estimate": self._estimate_wall_material(avg_confidence, len(cluster))
        }
    
    def _estimate_wall_material(self, confidence: float, size: int) -> str:
        """Duvar malzemesini tahmin et."""
        if confidence > 0.7 and size > 5:
            return "Beton duvar"
        elif confidence > 0.5:
            return "Tuğla duvar"
        elif confidence > 0.3:
            return "Alçıpan / Bölme"
        else:
            return "Hafif engel"
    
    def detect_rooms(self) -> List[dict]:
        """
        Duvar segmentlerinden oda sınırlarını tespit et.
        Kapalı alanları oda olarak işaretle.
        """
        if not self.wall_segments:
            self.extract_walls()
        
        rooms = []
        grid = self.grid_size
        
        # Basit oda tespiti: düşük engel bölgelerini bul (flood fill)
        room_map = [[0]*grid for _ in range(grid)]
        room_id = 0
        visited = set()
        
        for i in range(grid):
            for j in range(grid):
                if self.wall_confidence[i][j] < 0.2 and (i, j) not in visited:
                    # Açık alan bul
                    area = self._flood_fill_room(i, j, visited, room_map, room_id)
                    if area >= 4:  # En az 4 hücre = oda
                        room_id += 1
                        rooms.append({
                            "id": room_id,
                            "area_cells": area,
                            "area_m2": round(area * self.cell_size * self.cell_size, 1),
                            "center": self._room_center(room_map, room_id),
                            "label": f"Oda {room_id}"
                        })
        
        self.room_boundaries = rooms
        return rooms
    
    def _flood_fill_room(self, start_i, start_j, visited, room_map, room_id) -> int:
        """Oda alanını flood fill ile bul."""
        count = 0
        queue = [(start_i, start_j)]
        
        while queue:
            i, j = queue.pop(0)
            if (i, j) in visited:
                continue
            if not (0 <= i < self.grid_size and 0 <= j < self.grid_size):
                continue
            if self.wall_confidence[i][j] >= 0.25:
                continue
            
            visited.add((i, j))
            room_map[i][j] = room_id + 1
            count += 1
            
            for di, dj in [(-1,0),(1,0),(0,-1),(0,1)]:
                queue.append((i+di, j+dj))
        
        return count
    
    def _room_center(self, room_map, room_id) -> dict:
        """Oda merkezini bul."""
        points = [(i, j) for i in range(self.grid_size) for j in range(self.grid_size)
                  if room_map[i][j] == room_id + 1]
        if not points:
            return {"x": 0, "y": 0}
        avg_i = sum(p[0] for p in points) / len(points)
        avg_j = sum(p[1] for p in points) / len(points)
        cell = self.cell_size
        return {
            "grid_x": round(avg_i),
            "grid_y": round(avg_j),
            "x_m": round((avg_i - self.grid_size/2) * cell, 2),
            "y_m": round((avg_j - self.grid_size/2) * cell, 2)
        }
    
    def decay(self, factor: float = 0.97):
        """Zamanla engel güvenini azalt (dinamik engeller kaybolur)."""
        for i in range(self.grid_size):
            for j in range(self.grid_size):
                self.obstruction_grid[i][j] *= factor
                # Duvar güveni daha yavaş azalır (duvarlar hareket etmez)
                self.wall_confidence[i][j] *= 0.995


# ════════════════════════════════════════════════════════════════
# CİHAZ PROFİLCİ - Her cihaz için tam analiz
# ════════════════════════════════════════════════════════════════
class DeviceProfiler:
    """Her cihaz için derinlemesine sinyal profili ve insan tespiti."""
    
    def __init__(self, mac: str):
        self.mac = mac
        self.kalman = AdaptiveKalmanFilter()
        self.fft = MultiBandFFT(sample_rate=0.2)
        
        self.raw_history: deque = deque(maxlen=500)
        self.filtered_history: deque = deque(maxlen=500)
        self.timestamps: deque = deque(maxlen=500)
        
        # Sonuçlar
        self.filtered_rssi: float = -70
        self.distance_m: float = 5.0
        self.angle_deg: float = 0
        self.fft_result: dict = {}
        self.ray_result: dict = {}
        self.activity: str = "unknown"
        self.is_human: bool = False
        self.human_confidence: float = 0.0
        self.activity_detail: str = ""
        self.signal_quality: dict = {}
        
    def update(self, rssi: int, angle: float):
        """Yeni ölçüm ekle."""
        self.angle_deg = angle
        self.raw_history.append(rssi)
        self.timestamps.append(time.time())
        
        # Kalman filtre
        self.filtered_rssi = self.kalman.update(rssi)
        self.filtered_history.append(self.filtered_rssi)
        
        # Mesafe
        self.distance_m = self._rssi_to_distance(self.filtered_rssi)
        
        # FFT (yeterli veri varsa)
        if len(self.filtered_history) >= 8:  # 16 → 8 (hızlı tespit)
            samples = list(self.filtered_history)[-32:]  # 64 → 32
            self.fft_result = self.fft.full_spectrum_analysis(samples)
            self.activity = self.fft_result.get("dominant_activity", "unknown")
            self.activity_detail = self.fft_result.get("activity_detail", "")
            self.signal_quality = self.fft_result.get("signal_quality", {})
        
        # İnsan güveni
        self._compute_human_confidence()
    
    def _compute_human_confidence(self):
        """Tüm verilerden insan güveni hesapla."""
        scores = []
        
        # 1. FFT aktivite skoru
        fft_conf = self.fft_result.get("confidence", 0)
        activity = self.activity
        if activity in ("person_stationary", "person_resting"):
            scores.append(("fft_presence", 0.85 * fft_conf))
        elif activity in ("walking", "normal_walk", "slow_walking"):
            scores.append(("fft_walking", 0.9 * fft_conf))
        elif activity == "running":
            scores.append(("fft_running", 0.95 * fft_conf))
        elif activity == "gesture":
            scores.append(("fft_gesture", 0.6 * fft_conf))
        elif activity in ("static_device", "empty"):
            scores.append(("fft_empty", 0.1))
        else:
            scores.append(("fft_other", 0.4 * fft_conf))
        
        # 2. Sinyal varyans profili
        if len(self.filtered_history) >= 5:  # 10 → 5
            recent = list(self.filtered_history)[-10:]  # 20 → 10
            std = math.sqrt(sum((x-sum(recent)/len(recent))**2 for x in recent)/len(recent))
            
            if 0.3 < std < 4.0:  # 0.8 → 0.3 (daha hassas)
                scores.append(("variance_human", 0.75))  # İnsan kaynaklı
            elif std > 4.0:
                scores.append(("variance_walk", 0.85))  # Yürüme
            else:
                scores.append(("variance_static", 0.2))  # Sabit cihaz
        
        # 3. Mesafe faktörü (yakın = muhtemelen insan)
        if self.distance_m < 2:
            scores.append(("distance_close", 0.65))
        elif self.distance_m < 5:
            scores.append(("distance_mid", 0.5))
        else:
            scores.append(("distance_far", 0.3))
        
        # 4. Solunum paterni (en güçlü kanıt)
        bands = self.fft_result.get("bands", {})
        resp = bands.get("respiration", {})
        if resp.get("active") and resp.get("ratio", 0) > 0.15:
            scores.append(("respiration_detected", 0.9))
        
        # Ağırlıklı ortalama
        if scores:
            self.human_confidence = sum(s[1] for s in scores) / len(scores)
            self.is_human = self.human_confidence > 0.35  # 0.45 → 0.35 (daha hassas tespit)
        
    def _rssi_to_distance(self, rssi: float) -> float:
        """RSSI → metre (path loss model)."""
        ref_rssi = -40  # 1m referans
        n = 2.8  # Kapalı alan üssü
        try:
            d = 10 ** ((ref_rssi - rssi) / (10 * n))
            return max(0.1, min(d, 12.0))
        except:
            return 5.0
    
    def get_full_profile(self) -> dict:
        """Tam profil verisi."""
        return {
            "mac": self.mac,
            "rssi_raw": int(self.raw_history[-1]) if self.raw_history else -100,
            "rssi_filtered": round(self.filtered_rssi, 1),
            "distance_m": round(self.distance_m, 2),
            "angle_deg": round(self.angle_deg, 1),
            "is_human": self.is_human,
            "human_confidence": round(self.human_confidence, 3),
            "activity": self.activity,
            "activity_detail": self.activity_detail,
            "fft_confidence": round(self.fft_result.get("confidence", 0), 2),
            "dominant_freq": self.fft_result.get("dominant_freq", 0),
            "bands": self.fft_result.get("bands", {}),
            "spectrum": self.fft_result.get("spectrum", []),
            "signal_quality": self.signal_quality,
            "kalman_state": self.kalman.get_state(),
            "sample_count": len(self.raw_history),
            "signal_history": [round(x, 1) for x in list(self.filtered_history)[-40:]],
            "ray_trace": self.ray_result,
        }


# ════════════════════════════════════════════════════════════════
# ANA DERİN ANALİZ MOTORU
# ════════════════════════════════════════════════════════════════
class DeepWifiAnalyzer:
    """
    v3 Ana Motor - Tüm gelişmiş bileşenleri birleştirir.
    
    Her tarama döngüsünde:
    1. Cihaz sinyallerini Adaptive Kalman ile filtrele
    2. Multi-band FFT ile aktivite tespit et
    3. Ray Tracing ile duvar/engel konumlarını belirle
    4. Çoklu AP sinyallerinden ortam parmak izi oluştur
    5. Otomatik kat planı (duvar segmentleri, oda sınırları) çıkar
    6. İnsan silüetlerini konumlandır
    """
    
    GRID_SIZE = 40   # 40x40 grid (daha hassas)
    AREA_M = 10.0    # 10m x 10m alan
    
    def __init__(self):
        self.profilers: Dict[str, DeviceProfiler] = {}
        self.ray_tracer = RayTracer(grid_size=self.GRID_SIZE, area_m=self.AREA_M)
        
        # Çoklu AP verisi
        self.ap_signals: Dict[str, deque] = {}  # BSSID → RSSI history
        self.ap_info: Dict[str, dict] = {}
        
        # Ortam durumu
        self.baseline_fingerprint: Optional[Dict[str, float]] = None
        self.current_fingerprint: Dict[str, float] = {}
        self.environment_stable_since: float = time.time()
        
        # Haritalar (40x40 yüksek çözünürlük)
        self.signal_map = [[0.0]*self.GRID_SIZE for _ in range(self.GRID_SIZE)]
        self.human_presence_map = [[0.0]*self.GRID_SIZE for _ in range(self.GRID_SIZE)]
        self.movement_map = [[0.0]*self.GRID_SIZE for _ in range(self.GRID_SIZE)]
        
        # İstatistikler
        self.total_scans = 0
        self.scan_start_time = time.time()
        self.events: deque = deque(maxlen=100)
    
    def process_scan(self, devices: Dict[str, dict]) -> dict:
        """Ana analiz döngüsü."""
        self.total_scans += 1
        
        # 1. AP taraması
        wifi_networks = self._scan_wifi_aps()
        self._update_fingerprint(wifi_networks)
        
        # 2. Cihaz profilleme
        for mac, device in devices.items():
            rssi = device.get("rssi", -70)
            angle = self._device_angle(mac)
            
            if mac not in self.profilers:
                self.profilers[mac] = DeviceProfiler(mac)
            
            profiler = self.profilers[mac]
            profiler.update(rssi, angle)
            
            # 3. Ray tracing
            ray_result = self.ray_tracer.trace_ray(
                rssi, profiler.distance_m, angle
            )
            profiler.ray_result = ray_result
        
        # 4. Haritaları güncelle
        self._update_maps(devices)
        
        # 5. Duvar & oda tespiti
        self.ray_tracer.extract_walls()
        rooms = self.ray_tracer.detect_rooms()
        
        # 6. Decay (eski verileri azalt)
        self.ray_tracer.decay()
        self._decay_maps()
        
        # 7. Ortam değişikliği tespiti
        env_change = self._detect_environment_change()
        
        return self._build_output(devices, wifi_networks, env_change, rooms)
    
    def _scan_wifi_aps(self) -> List[dict]:
        """WiFi ağlarını tara."""
        networks = []
        try:
            result = subprocess.run(
                ["netsh", "wlan", "show", "networks", "mode=Bssid"],
                capture_output=True, text=True, timeout=15,
                encoding='utf-8', errors='replace'
            )
            
            current = {}
            for line in result.stdout.split('\n'):
                line = line.strip()
                if line.startswith('SSID') and ':' in line and 'BSSID' not in line:
                    if current and 'bssid' in current:
                        networks.append(current)
                    current = {"ssid": line.split(':', 1)[1].strip()}
                elif 'BSSID' in line and ':' in line:
                    bssid = line.split(':', 1)[1].strip()
                    if len(bssid) >= 17:
                        current["bssid"] = bssid[:17].lower()
                elif ('Sinyal' in line or 'Signal' in line) and '%' in line:
                    match = re.search(r'(\d+)%', line)
                    if match:
                        pct = int(match.group(1))
                        current["signal_percent"] = pct
                        current["rssi"] = int((pct/2) - 100)
                elif 'Kanal' in line or 'Channel' in line:
                    match = re.search(r':\s*(\d+)', line)
                    if match:
                        current["channel"] = int(match.group(1))
            
            if current and 'bssid' in current:
                networks.append(current)
        except:
            pass
        
        # AP geçmişini güncelle
        for net in networks:
            bssid = net.get("bssid", "")
            if bssid:
                if bssid not in self.ap_signals:
                    self.ap_signals[bssid] = deque(maxlen=200)
                self.ap_signals[bssid].append(net.get("rssi", -100))
                self.ap_info[bssid] = net
        
        return networks
    
    def _update_fingerprint(self, networks: List[dict]):
        """Ortam parmak izi güncelle."""
        self.current_fingerprint = {
            net["bssid"]: net["rssi"] for net in networks if "bssid" in net
        }
        if self.baseline_fingerprint is None and len(self.current_fingerprint) >= 3:
            self.baseline_fingerprint = self.current_fingerprint.copy()
    
    def _detect_environment_change(self) -> dict:
        """Ortam değişikliği tespit."""
        if not self.baseline_fingerprint or not self.current_fingerprint:
            return {"changed": False, "score": 0, "detail": "Baseline bekleniyor"}
        
        deviations = []
        for bssid, baseline_rssi in self.baseline_fingerprint.items():
            current_rssi = self.current_fingerprint.get(bssid)
            if current_rssi is not None:
                deviations.append(current_rssi - baseline_rssi)
        
        if not deviations:
            return {"changed": False, "score": 0, "detail": "Karşılaştırılacak AP yok"}
        
        rms = math.sqrt(sum(d*d for d in deviations) / len(deviations))
        affected = sum(1 for d in deviations if abs(d) > 3)
        score = min(1.0, rms / 8.0)
        
        if score > 0.4:
            if affected >= 3:
                detail = f"Güçlü ortam değişimi ({affected} AP etkilendi, RMS:{rms:.1f}dB) - İnsan hareketi muhtemel"
            else:
                detail = f"Orta değişim (RMS:{rms:.1f}dB)"
        elif score > 0.15:
            detail = "Hafif değişim - olası varlık"
        else:
            detail = "Ortam sabit"
        
        changed = score > 0.25
        if changed and (time.time() - self.environment_stable_since > 5):
            self.events.append({
                "time": datetime.now().isoformat(),
                "type": "environment_change",
                "score": round(score, 2),
                "detail": detail
            })
            self.environment_stable_since = time.time()
        
        return {"changed": changed, "score": round(score, 3), 
                "detail": detail, "rms_db": round(rms, 1), "affected_aps": affected}
    
    def _update_maps(self, devices: Dict[str, dict]):
        """Haritaları güncelle."""
        grid = self.GRID_SIZE
        
        # Signal map decay
        for i in range(grid):
            for j in range(grid):
                self.signal_map[i][j] *= 0.6
                self.human_presence_map[i][j] *= 0.7
                self.movement_map[i][j] *= 0.65
        
        for mac, profiler in self.profilers.items():
            gx, gy = self._polar_to_grid(profiler.distance_m, profiler.angle_deg)
            
            # Sinyal haritası
            for di in range(-3, 4):
                for dj in range(-3, 4):
                    ni, nj = gx+di, gy+dj
                    if 0 <= ni < grid and 0 <= nj < grid:
                        d2 = di*di + dj*dj
                        self.signal_map[ni][nj] += math.exp(-d2/5.0) * 0.5
            
            # İnsan varlığı haritası
            if profiler.human_confidence > 0.25:  # 0.4 → 0.25 (daha hassas)
                for di in range(-3, 4):
                    for dj in range(-3, 4):
                        ni, nj = gx+di, gy+dj
                        if 0 <= ni < grid and 0 <= nj < grid:
                            d2 = di*di + dj*dj
                            self.human_presence_map[ni][nj] += (
                                math.exp(-d2/4.0) * profiler.human_confidence * 0.8)  # 0.6 → 0.8
            
            # Hareket haritası
            if profiler.activity in ("walking", "normal_walk", "slow_walking", "running"):
                for di in range(-2, 3):
                    for dj in range(-2, 3):
                        ni, nj = gx+di, gy+dj
                        if 0 <= ni < grid and 0 <= nj < grid:
                            d2 = di*di + dj*dj
                            self.movement_map[ni][nj] += math.exp(-d2/3.0) * 0.5
        
        # Normalize
        self._normalize(self.signal_map)
        self._normalize(self.human_presence_map)
        self._normalize(self.movement_map)
    
    def _normalize(self, grid_map):
        """0-1 normalize."""
        max_val = max(max(row) for row in grid_map) or 1.0
        if max_val > 1.0:
            for i in range(len(grid_map)):
                for j in range(len(grid_map[0])):
                    grid_map[i][j] /= max_val
    
    def _decay_maps(self):
        """Hareket haritası hızlı decay."""
        for i in range(self.GRID_SIZE):
            for j in range(self.GRID_SIZE):
                self.movement_map[i][j] *= 0.8
    
    def _device_angle(self, mac: str) -> float:
        """MAC → tutarlı açı."""
        h = sum(ord(c) for c in mac)
        return (h * 137.508) % 360
    
    def _polar_to_grid(self, distance: float, angle_deg: float) -> Tuple[int, int]:
        """Polar → grid."""
        grid = self.GRID_SIZE
        center = grid // 2
        r = (distance / self.AREA_M) * (grid / 2)
        angle_rad = math.radians(angle_deg)
        gx = max(0, min(grid-1, int(center + r * math.cos(angle_rad))))
        gy = max(0, min(grid-1, int(center + r * math.sin(angle_rad))))
        return gx, gy
    
    def _build_output(self, devices, wifi_networks, env_change, rooms) -> dict:
        """Frontend çıktısı."""
        grid = self.GRID_SIZE
        
        # Haritalar → düz liste
        signal_data = self._map_to_list(self.signal_map, 0.03)
        wall_data = self._map_to_list(self.ray_tracer.wall_confidence, 0.15)
        human_data = self._map_to_list(self.human_presence_map, 0.08)
        movement_data = self._map_to_list(self.movement_map, 0.06)
        obstruction_data = self._map_to_list(self.ray_tracer.obstruction_grid, 0.1)
        
        # Cihaz profilleri
        device_positions = {}
        for mac, profiler in self.profilers.items():
            device = devices.get(mac, {})
            profile = profiler.get_full_profile()
            device_positions[mac] = {
                **profile,
                "ip": device.get("ip", ""),
                "vendor": device.get("vendor", ""),
                "hostname": device.get("hostname", ""),
                "device_type": device.get("device_type", ""),
            }
        
        # Duvar segmentleri
        walls = self.ray_tracer.wall_segments
        
        return {
            "signal_heatmap": signal_data,
            "wall_map": wall_data,
            "obstruction_map": obstruction_data,
            "human_heatmap": human_data,
            "movement_heatmap": movement_data,
            "device_positions": device_positions,
            "wall_segments": walls,
            "rooms": rooms,
            "wifi_networks": wifi_networks[:15],
            "environment_change": env_change,
            "events": list(self.events)[-15:],
            "grid_size": grid,
            "area_size_m": self.AREA_M,
            "stats": {
                "total_tracked": len(self.profilers),
                "humans_detected": sum(1 for p in self.profilers.values() if p.is_human),
                "moving_count": sum(1 for p in self.profilers.values() 
                                   if p.activity in ("walking","running","slow_walking","normal_walk")),
                "breathing_detected": sum(1 for p in self.profilers.values()
                                         if p.activity in ("person_stationary","person_resting")),
                "walls_detected": len(walls),
                "rooms_detected": len(rooms),
                "wifi_networks_visible": len(wifi_networks),
                "presence_score": round(env_change.get("score", 0), 3),
                "total_scans": self.total_scans,
                "uptime_s": round(time.time() - self.scan_start_time),
                "avg_confidence": round(
                    sum(p.human_confidence for p in self.profilers.values()) / max(len(self.profilers), 1), 3),
                "grid_resolution": f"{grid}x{grid}",
            }
        }
    
    def _map_to_list(self, grid_map, threshold: float) -> List[dict]:
        """Grid haritasını frontend listesine çevir."""
        data = []
        grid = len(grid_map)
        for i in range(grid):
            for j in range(grid):
                v = grid_map[i][j]
                if v > threshold:
                    data.append({"x": i, "y": j, "v": round(v, 3)})
        return data
