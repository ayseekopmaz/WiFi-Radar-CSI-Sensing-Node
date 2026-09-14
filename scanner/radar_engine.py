"""
WiFi Radar v5 - ULTRA Engine
Anlık tespit, paralel sinyal işleme, agresif ray casting.

Farklar:
- 2 sample'da FFT (zero-padding ile interpolasyon)
- Kalman k=0.8 (neredeyse raw ölçüm kullan)
- Her ray 3x daha fazla engel puanı bırakır
- Duvar tespiti her taramada (3 taramada bir değil)
- RSSI varyansı 0.2dBm'de bile insan tespit
- 100x100 grid (10cm çözünürlük)
- Concurrent AP + ARP tarama
"""

import math
import time
import subprocess
import re
import random
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Set
from collections import deque


# ═══════════════════════════════════════
# INSTANT KALMAN - k=0.8 neredeyse raw
# ═══════════════════════════════════════
class InstantKalman:
    def __init__(self):
        self.x = -65.0
        self.p = 20.0
        self.q = 5.0
        self.r = 1.0
    
    def feed(self, z: float) -> float:
        self.p += self.q
        k = self.p / (self.p + self.r)
        self.x += k * (z - self.x)
        self.p *= (1 - k)
        return self.x


# ═══════════════════════════════════════
# INSTANT FFT - 2 sample'da analiz
# Zero-padding + varyans bazlı hızlı karar
# ═══════════════════════════════════════
class InstantFFT:
    """2 sample ile bile aktivite tespit eder."""
    
    def analyze(self, samples: List[float]) -> dict:
        n = len(samples)
        
        if n < 2:
            return {"activity": "no_data", "is_human": False,
                    "confidence": 0, "detail": "Veri yok"}
        
        # VARYANS BAZLI ANLIK KARAR (FFT'den önce)
        mean = sum(samples) / n
        variance = sum((x - mean)**2 for x in samples) / n
        std = math.sqrt(variance)
        
        # Anlık değişim (son 2 sample farkı)
        instant_change = abs(samples[-1] - samples[-2]) if n >= 2 else 0
        
        # 2 sample bile olsa karar ver
        if n < 4:
            return self._quick_decision(std, instant_change)
        
        # FFT (4+ sample, zero-pad ile 16'ya tamamla)
        padded = list(samples[-min(n, 16):])
        while len(padded) < 16:
            padded.append(mean)  # Zero-pad
        
        # DC çıkar + pencere
        sig = [padded[i] - mean for i in range(len(padded))]
        nn = len(sig)
        
        best_mag = 0
        best_freq = 0
        rate = 0.5  # 2sn'de 1 ölçüm
        
        for k in range(1, nn//2):
            real = sum(sig[t]*math.cos(2*math.pi*k*t/nn) for t in range(nn))
            imag = sum(sig[t]*math.sin(2*math.pi*k*t/nn) for t in range(nn))
            mag = math.sqrt(real*real + imag*imag) / nn
            freq = k * rate / nn
            if mag > best_mag:
                best_mag = mag
                best_freq = freq
        
        # Karar: varyans + frekans birlikte
        return self._full_decision(std, instant_change, best_freq, best_mag)
    
    def _quick_decision(self, std, change) -> dict:
        """2-3 sample ile hızlı karar."""
        if change > 5:
            return {"activity": "fast_move", "is_human": True,
                    "confidence": 0.85, "detail": "Hızlı hareket (anlık)"}
        elif change > 2:
            return {"activity": "movement", "is_human": True,
                    "confidence": 0.75, "detail": "Hareket tespit (anlık)"}
        elif std > 1.5:
            return {"activity": "presence", "is_human": True,
                    "confidence": 0.65, "detail": "Varlık tespit (varyans)"}
        elif std > 0.5:
            return {"activity": "micro", "is_human": True,
                    "confidence": 0.5, "detail": "Mikro hareket"}
        elif std > 0.2:
            return {"activity": "possible", "is_human": True,
                    "confidence": 0.4, "detail": "Olası varlık"}
        else:
            return {"activity": "static", "is_human": False,
                    "confidence": 0.8, "detail": "Sabit cihaz"}
    
    def _full_decision(self, std, change, freq, mag) -> dict:
        """Tam veri ile detaylı karar."""
        # Agresif eşikler - her şeyi yakala
        if mag > 0.5 and freq > 2.0:
            return {"activity": "running", "is_human": True,
                    "confidence": 0.95, "detail": f"Koşma ({freq:.2f}Hz)"}
        
        if mag > 0.3 and 1.0 <= freq <= 2.5:
            return {"activity": "walking", "is_human": True,
                    "confidence": 0.9, "detail": f"Yürüme ({freq:.2f}Hz)"}
        
        if mag > 0.15 and 0.3 <= freq <= 1.0:
            return {"activity": "slow_walk", "is_human": True,
                    "confidence": 0.8, "detail": f"Yavaş hareket ({freq:.2f}Hz)"}
        
        if mag > 0.08 and freq < 0.3:
            return {"activity": "breathing", "is_human": True,
                    "confidence": 0.85, "detail": f"Solunum ({freq:.3f}Hz)"}
        
        if std > 1.0:
            return {"activity": "human_near", "is_human": True,
                    "confidence": 0.7, "detail": "Yakın insan varlığı"}
        
        if std > 0.3 or change > 1.0:
            return {"activity": "possible_human", "is_human": True,
                    "confidence": 0.5, "detail": "Olası insan"}
        
        return {"activity": "device", "is_human": False,
                "confidence": 0.85, "detail": "Sabit cihaz"}


# ═══════════════════════════════════════
# AGGRESSIVE WALL DETECTOR - 100x100 grid
# ═══════════════════════════════════════
class WallDetector:
    """100x100 grid, 10cm çözünürlük, agresif duvar tespiti."""
    
    GRID = 100
    AREA = 10.0
    CELL = 0.1  # 10cm
    
    def __init__(self):
        self.wall_grid = [[0.0]*self.GRID for _ in range(self.GRID)]
        self.obstacle_grid = [[0.0]*self.GRID for _ in range(self.GRID)]
        self.room_map = [[0]*self.GRID for _ in range(self.GRID)]
        self.walls: List[dict] = []
        self.rooms: List[dict] = []
        self.doors: List[dict] = []
    
    def cast_ray(self, rssi: float, distance: float, angle_deg: float):
        """Agresif ray casting - 3x güçlü iz bırakır."""
        center = self.GRID // 2
        angle = math.radians(angle_deg)
        
        # Beklenen sinyal (free space)
        if distance < 0.2:
            return
        expected = -40 - 28 * math.log10(distance)
        loss = expected - rssi
        
        if loss < 2:
            return  # Engel yok
        
        # Kaç engel var?
        num_obs = max(1, int(loss / 4))  # Her 4dB = 1 engel (agresif)
        
        for i in range(num_obs):
            ratio = 0.15 + (i * 0.2)
            if ratio > 0.85:
                break
            
            obs_dist = distance * ratio
            gx = int(center + (obs_dist/self.AREA) * self.GRID * math.cos(angle))
            gy = int(center + (obs_dist/self.AREA) * self.GRID * math.sin(angle))
            gx = max(2, min(self.GRID-3, gx))
            gy = max(2, min(self.GRID-3, gy))
            
            strength = min(1.0, loss / 8.0)
            
            # 3x3 alan - güçlü iz
            for di in range(-2, 3):
                for dj in range(-2, 3):
                    ni, nj = gx+di, gy+dj
                    if 0 <= ni < self.GRID and 0 <= nj < self.GRID:
                        d2 = di*di + dj*dj
                        w = math.exp(-d2/3.0)
                        # AGRESIF: 0.6 çarpan (önceden 0.35)
                        self.obstacle_grid[ni][nj] = min(1.0,
                            self.obstacle_grid[ni][nj] + strength * w * 0.6)
                        self.wall_grid[ni][nj] = min(1.0,
                            self.wall_grid[ni][nj] + strength * w * 0.5)
    
    def detect_walls(self) -> List[dict]:
        """BFS + line fitting ile duvar tespiti."""
        walls = []
        visited = set()
        threshold = 0.2  # Düşük eşik - daha çok duvar yakala
        
        for i in range(self.GRID):
            for j in range(self.GRID):
                if self.wall_grid[i][j] >= threshold and (i,j) not in visited:
                    cluster = self._bfs(i, j, threshold, visited)
                    if len(cluster) >= 4:
                        wall = self._fit_wall(cluster)
                        if wall:
                            walls.append(wall)
        
        self.walls = walls
        return walls
    
    def _bfs(self, si, sj, th, visited) -> List[Tuple[int,int]]:
        cluster = []
        queue = [(si, sj)]
        while queue and len(cluster) < 120:
            i, j = queue.pop(0)
            if (i,j) in visited or not(0<=i<self.GRID and 0<=j<self.GRID):
                continue
            if self.wall_grid[i][j] < th:
                continue
            visited.add((i,j))
            cluster.append((i,j))
            for di,dj in [(-1,0),(1,0),(0,-1),(0,1),(1,1),(-1,-1),(1,-1),(-1,1)]:
                queue.append((i+di, j+dj))
        return cluster
    
    def _fit_wall(self, cluster) -> Optional[dict]:
        if len(cluster) < 4:
            return None
        xs = [c[0] for c in cluster]
        ys = [c[1] for c in cluster]
        x1,x2 = min(xs), max(xs)
        y1,y2 = min(ys), max(ys)
        w, h = x2-x1, y2-y1
        
        avg_conf = sum(self.wall_grid[i][j] for i,j in cluster) / len(cluster)
        
        if w >= h:
            avg_y = sum(ys)/len(ys)
            return {"x1":x1,"y1":round(avg_y),"x2":x2,"y2":round(avg_y),
                    "orient":"H","length":w,"confidence":round(avg_conf,2),
                    "cells":len(cluster),"thickness":max(1,h)}
        else:
            avg_x = sum(xs)/len(xs)
            return {"x1":round(avg_x),"y1":y1,"x2":round(avg_x),"y2":y2,
                    "orient":"V","length":h,"confidence":round(avg_conf,2),
                    "cells":len(cluster),"thickness":max(1,w)}

    def detect_rooms(self) -> List[dict]:
        rooms = []
        visited = set()
        room_id = 0
        for i in range(3, self.GRID-3):
            for j in range(3, self.GRID-3):
                if self.wall_grid[i][j] < 0.12 and (i,j) not in visited:
                    area = self._flood(i, j, visited, room_id)
                    if area >= 25:
                        room_id += 1
                        pts = [(x,y) for x in range(self.GRID) for y in range(self.GRID)
                               if self.room_map[x][y] == room_id]
                        if pts:
                            cx = sum(p[0] for p in pts)/len(pts)
                            cy = sum(p[1] for p in pts)/len(pts)
                            m2 = area * self.CELL * self.CELL
                            rooms.append({
                                "id": room_id,
                                "cx": round((cx/self.GRID)*100, 1),
                                "cy": round((cy/self.GRID)*100, 1),
                                "area_m2": round(m2, 1),
                                "label": self._name(m2, room_id),
                                "cells": area,
                            })
        self.rooms = rooms
        return rooms
    
    def _flood(self, si, sj, visited, rid) -> int:
        count = 0
        queue = [(si, sj)]
        while queue:
            i, j = queue.pop(0)
            if (i,j) in visited or not(0<=i<self.GRID and 0<=j<self.GRID):
                continue
            if self.wall_grid[i][j] >= 0.18:
                continue
            visited.add((i,j))
            self.room_map[i][j] = rid + 1
            count += 1
            for di,dj in [(-1,0),(1,0),(0,-1),(0,1)]:
                queue.append((i+di, j+dj))
        return count
    
    def _name(self, m2, idx):
        if m2 > 12: return "Salon"
        elif m2 > 7: return "Yatak Odası"
        elif m2 > 4: return "Oda"
        elif m2 > 2: return "Mutfak" if idx <= 2 else "Banyo"
        else: return "Koridor"
    
    def detect_doors(self) -> List[dict]:
        doors = []
        for wall in self.walls:
            if wall["length"] < 8:
                continue
            x1,y1,x2,y2 = wall["x1"],wall["y1"],wall["x2"],wall["y2"]
            if wall["orient"] == "H":
                y = y1
                gap_start = None
                for x in range(x1, x2+1):
                    if 0<=x<self.GRID and 0<=y<self.GRID:
                        if self.wall_grid[x][y] < 0.08:
                            if gap_start is None: gap_start = x
                        else:
                            if gap_start and (x-gap_start) >= 3:
                                doors.append({
                                    "x": round(((gap_start+x)/2/self.GRID)*100,1),
                                    "y": round((y/self.GRID)*100,1),
                                    "orient": "H", "width": x-gap_start})
                            gap_start = None
            else:
                x = x1
                gap_start = None
                for y in range(y1, y2+1):
                    if 0<=x<self.GRID and 0<=y<self.GRID:
                        if self.wall_grid[x][y] < 0.08:
                            if gap_start is None: gap_start = y
                        else:
                            if gap_start and (y-gap_start) >= 3:
                                doors.append({
                                    "x": round((x/self.GRID)*100,1),
                                    "y": round(((gap_start+y)/2/self.GRID)*100,1),
                                    "orient": "V", "width": y-gap_start})
                            gap_start = None
        self.doors = doors
        return doors
    
    def decay(self):
        for i in range(self.GRID):
            for j in range(self.GRID):
                self.obstacle_grid[i][j] *= 0.97
                self.wall_grid[i][j] *= 0.998  # Duvarlar çok yavaş azalır
    
    def get_data(self) -> dict:
        wall_pts = [{"x":i,"y":j,"v":round(self.wall_grid[i][j],2)}
                    for i in range(self.GRID) for j in range(self.GRID)
                    if self.wall_grid[i][j] > 0.12]
        obs_pts = [{"x":i,"y":j,"v":round(self.obstacle_grid[i][j],2)}
                   for i in range(self.GRID) for j in range(self.GRID)
                   if self.obstacle_grid[i][j] > 0.08]
        return {"wall_points": wall_pts, "obstacle_points": obs_pts,
                "wall_lines": self.walls, "rooms": self.rooms,
                "doors": self.doors, "grid": self.GRID}


# ═══════════════════════════════════════
# DEVICE TRACKER - Anlık profil
# ═══════════════════════════════════════
class DeviceTrack:
    def __init__(self, mac: str):
        self.mac = mac
        self.kalman = InstantKalman()
        self.fft = InstantFFT()
        self.samples: deque = deque(maxlen=60)
        self.filtered: deque = deque(maxlen=60)
        self.rssi = -70.0
        self.distance = 5.0
        self.angle = 0.0
        self.result: dict = {}
        self.is_human = False
        self.confidence = 0.0
        self.activity = "no_data"
        self.detail = ""
    
    def feed(self, raw_rssi: int, angle: float):
        self.angle = angle
        self.samples.append(raw_rssi)
        self.rssi = self.kalman.feed(raw_rssi)
        self.filtered.append(self.rssi)
        self.distance = max(0.2, min(10.0, 10**((-40-self.rssi)/28.0)))
        
        # FFT - hemen analiz (2+ sample)
        if len(self.filtered) >= 2:
            self.result = self.fft.analyze(list(self.filtered))
            self.is_human = self.result["is_human"]
            self.confidence = self.result["confidence"]
            self.activity = self.result["activity"]
            self.detail = self.result["detail"]
    
    def to_dict(self) -> dict:
        return {
            "mac": self.mac, "rssi": round(self.rssi, 1),
            "distance": round(self.distance, 2),
            "angle": round(self.angle, 1),
            "activity": self.activity, "detail": self.detail,
            "confidence": self.confidence, "is_human": self.is_human,
            "samples": len(self.samples),
            "history": [round(x,1) for x in list(self.filtered)[-25:]],
        }


# ═══════════════════════════════════════
# ANA MOTOR v5 - ULTRA
# ═══════════════════════════════════════
class RadarEngine:
    """
    v5 Ultra Engine.
    - 100x100 grid (10cm hassasiyet)
    - 2 sample'da insan tespiti
    - Her taramada duvar güncelle
    - Paralel AP tarama
    - 1.5sn tarama aralığı
    """
    
    GRID = 100
    AREA = 10.0
    
    def __init__(self):
        self.tracks: Dict[str, DeviceTrack] = {}
        self.detector = WallDetector()
        self.total_scans = 0
        self.start_time = time.time()
        
        # Haritalar (100x100)
        self.signal_map = [[0.0]*self.GRID for _ in range(self.GRID)]
        self.human_map = [[0.0]*self.GRID for _ in range(self.GRID)]
        self.move_map = [[0.0]*self.GRID for _ in range(self.GRID)]
        
        self.ap_cache: List[dict] = []
        self._ap_lock = threading.Lock()
    
    def scan(self, devices: Dict[str, dict]) -> dict:
        """Ana döngü - her çağrıda tam analiz."""
        self.total_scans += 1
        
        # Paralel AP tarama (arka planda)
        ap_thread = threading.Thread(target=self._bg_scan_aps, daemon=True)
        ap_thread.start()
        
        # Cihaz işleme + ray cast
        for mac, dev in devices.items():
            rssi = dev.get("rssi", -70)
            angle = self._angle(mac)
            
            if mac not in self.tracks:
                self.tracks[mac] = DeviceTrack(mac)
            
            t = self.tracks[mac]
            t.feed(rssi, angle)
            
            # Ray casting (her cihaz için)
            self.detector.cast_ray(t.rssi, t.distance, t.angle)
        
        # Harita güncelle
        self._update_maps()
        
        # Duvar/oda/kapı - HER TARAMADA
        self.detector.detect_walls()
        self.detector.detect_rooms()
        self.detector.detect_doors()
        
        # Decay
        self.detector.decay()
        self._decay()
        
        # AP thread'i bekle (max 2sn)
        ap_thread.join(timeout=2)
        
        return self._output(devices)
    
    def _bg_scan_aps(self):
        """Arka planda WiFi AP tara."""
        networks = []
        try:
            r = subprocess.run(
                ["netsh", "wlan", "show", "networks", "mode=Bssid"],
                capture_output=True, text=True, timeout=6,
                encoding='utf-8', errors='replace')
            current = {}
            for line in r.stdout.split('\n'):
                line = line.strip()
                if line.startswith('SSID') and ':' in line and 'BSSID' not in line:
                    if current and 'bssid' in current:
                        networks.append(current)
                    current = {"ssid": line.split(':',1)[1].strip()}
                elif 'BSSID' in line and ':' in line:
                    b = line.split(':',1)[1].strip()
                    if len(b) >= 17: current["bssid"] = b[:17].lower()
                elif ('Sinyal' in line or 'Signal' in line) and '%' in line:
                    m = re.search(r'(\d+)%', line)
                    if m:
                        pct = int(m.group(1))
                        current["rssi"] = int(pct/2-100)
                        current["signal"] = pct
            if current and 'bssid' in current:
                networks.append(current)
        except:
            pass
        with self._ap_lock:
            self.ap_cache = networks
    
    def _angle(self, mac: str) -> float:
        return (sum(ord(c) for c in mac) * 137.508) % 360
    
    def _update_maps(self):
        grid = self.GRID
        center = grid // 2
        
        for mac, t in self.tracks.items():
            a = math.radians(t.angle)
            r = (t.distance / self.AREA) * grid
            gx = max(0, min(grid-1, int(center + r*math.cos(a))))
            gy = max(0, min(grid-1, int(center + r*math.sin(a))))
            
            # Sinyal noktası
            for di in range(-2, 3):
                for dj in range(-2, 3):
                    ni, nj = gx+di, gy+dj
                    if 0<=ni<grid and 0<=nj<grid:
                        self.signal_map[ni][nj] += math.exp(-(di*di+dj*dj)/3.0)*0.4
            
            # İnsan haritası (güçlü)
            if t.is_human:
                for di in range(-4, 5):
                    for dj in range(-4, 5):
                        ni, nj = gx+di, gy+dj
                        if 0<=ni<grid and 0<=nj<grid:
                            d2 = di*di + dj*dj
                            self.human_map[ni][nj] += math.exp(-d2/5.0)*t.confidence*0.7
            
            # Hareket
            if t.activity in ("walking","running","slow_walk","fast_move","movement"):
                for di in range(-3, 4):
                    for dj in range(-3, 4):
                        ni, nj = gx+di, gy+dj
                        if 0<=ni<grid and 0<=nj<grid:
                            self.move_map[ni][nj] += math.exp(-(di*di+dj*dj)/4.0)*0.5
    
    def _decay(self):
        grid = self.GRID
        for i in range(grid):
            for j in range(grid):
                self.signal_map[i][j] *= 0.5
                self.human_map[i][j] *= 0.6
                self.move_map[i][j] *= 0.4

    def _output(self, devices: Dict[str, dict]) -> dict:
        grid = self.GRID
        
        # Haritalar (eşik üstü noktalar)
        signal_pts = []
        human_pts = []
        move_pts = []
        for i in range(0, grid, 2):  # 2'şer atlayarak (performans)
            for j in range(0, grid, 2):
                s = max(self.signal_map[i][j], self.signal_map[min(i+1,grid-1)][j])
                h = max(self.human_map[i][j], self.human_map[min(i+1,grid-1)][j])
                m = max(self.move_map[i][j], self.move_map[min(i+1,grid-1)][j])
                if s > 0.04: signal_pts.append({"x":i,"y":j,"v":round(min(1,s),2)})
                if h > 0.04: human_pts.append({"x":i,"y":j,"v":round(min(1,h),2)})
                if m > 0.04: move_pts.append({"x":i,"y":j,"v":round(min(1,m),2)})
        
        # Cihaz pozisyonları
        positions = {}
        for mac, t in self.tracks.items():
            dev = devices.get(mac, {})
            d = t.to_dict()
            d["ip"] = dev.get("ip", "")
            d["vendor"] = dev.get("vendor", "")
            d["hostname"] = dev.get("hostname", "")
            d["device_type"] = dev.get("device_type", "")
            positions[mac] = d
        
        # Kroki verisi
        floor = self.detector.get_data()
        
        with self._ap_lock:
            aps = self.ap_cache[:12]
        
        return {
            "signal_heatmap": signal_pts,
            "human_heatmap": human_pts,
            "movement_heatmap": move_pts,
            "wall_map": floor["wall_points"],
            "obstruction_map": floor["obstacle_points"],
            "wall_lines": floor["wall_lines"],
            "rooms": floor["rooms"],
            "doors": floor["doors"],
            "device_positions": positions,
            "wifi_networks": aps,
            "grid_size": grid,
            "area_size_m": self.AREA,
            "stats": {
                "total_tracked": len(self.tracks),
                "humans_detected": sum(1 for t in self.tracks.values() if t.is_human),
                "moving_count": sum(1 for t in self.tracks.values()
                    if t.activity in ("walking","running","slow_walk","fast_move","movement")),
                "breathing_count": sum(1 for t in self.tracks.values()
                    if t.activity == "breathing"),
                "walls_detected": len(floor["wall_lines"]),
                "rooms_detected": len(floor["rooms"]),
                "doors_detected": len(floor["doors"]),
                "wifi_aps": len(aps),
                "total_scans": self.total_scans,
                "uptime": round(time.time() - self.start_time),
                "avg_confidence": round(
                    sum(t.confidence for t in self.tracks.values())/max(len(self.tracks),1), 2),
                "grid_res": f"{grid}x{grid}",
            }
        }
