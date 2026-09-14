"""
WiFi Radar - Konum Tahmin Modülü
RSSI değerlerini kullanarak cihazların yaklaşık konumunu tahmin eder.
Polar koordinat sistemi ile radar benzeri görselleştirme için konum hesaplar.
"""

import math
import time
import random
from typing import Dict, List, Optional, Tuple
from datetime import datetime


class LocationEstimator:
    """
    WiFi sinyal gücü (RSSI) kullanarak cihazların konumunu tahmin eder.
    
    Yöntemler:
    1. RSSI bazlı mesafe tahmini (FSPL - Free Space Path Loss)
    2. Hareket analizi (RSSI değişimleri ile yön tahmini)
    3. Polar koordinat yerleşimi (radar görünümü için)
    """

    # Referans değerler
    RSSI_REF = -40  # 1 metre mesafedeki referans RSSI (dBm)
    PATH_LOSS_EXPONENT = 2.7  # Kapalı alan path loss üssü (2.7 - 4.3 arası)
    FREQUENCY_MHZ = 2437  # WiFi 2.4GHz band merkez frekansı

    def __init__(self, room_width: float = 20.0, room_height: float = 20.0):
        """
        Args:
            room_width: Tahmini oda/alan genişliği (metre)
            room_height: Tahmini oda/alan yüksekliği (metre)
        """
        self.room_width = room_width
        self.room_height = room_height
        self.device_positions: Dict[str, dict] = {}
        self.position_history: Dict[str, List[dict]] = {}
        self.scanner_position = (room_width / 2, room_height / 2)  # Merkez

    def rssi_to_distance(self, rssi: int) -> float:
        """
        RSSI değerinden mesafe hesapla (metre).
        Log-distance path loss modeli kullanır.
        
        Formül: d = 10 ^ ((RSSI_ref - RSSI) / (10 * n))
        
        Args:
            rssi: Sinyal gücü (dBm, negatif değer)
            
        Returns:
            Tahmini mesafe (metre)
        """
        if rssi >= 0:
            return 0.1

        try:
            distance = 10 ** ((self.RSSI_REF - rssi) / (10 * self.PATH_LOSS_EXPONENT))
            return max(0.1, min(distance, 50.0))  # 0.1m - 50m arası sınırla
        except (ValueError, OverflowError):
            return 25.0

    def calculate_position_polar(self, mac: str, rssi: int, device_index: int = 0,
                                  total_devices: int = 1) -> dict:
        """
        Cihazın polar koordinat konumunu hesapla.
        Radar görünümü için açı ve mesafe döndürür.
        
        Cihazlar eşit açı aralıklarıyla dağıtılır, 
        mesafe RSSI'den hesaplanır.
        
        Args:
            mac: Cihaz MAC adresi
            rssi: Sinyal gücü
            device_index: Cihazın sıra numarası
            total_devices: Toplam cihaz sayısı
            
        Returns:
            Polar koordinat bilgisi
        """
        distance = self.rssi_to_distance(rssi)

        # Cihazın açısını hesapla
        if mac in self.device_positions and "angle" in self.device_positions[mac]:
            # Mevcut açıyı koru (tutarlılık için)
            angle = self.device_positions[mac]["angle"]
            # Küçük rastgele hareket ekle (canlılık için)
            angle += random.uniform(-2, 2)
            angle = angle % 360
        else:
            # Yeni cihaz - eşit dağılım + rastgelelik
            base_angle = (360 / max(total_devices, 1)) * device_index
            angle = (base_angle + random.uniform(-15, 15)) % 360

        # Kartezyen koordinatlara çevir (görselleştirme için)
        angle_rad = math.radians(angle)
        x = distance * math.cos(angle_rad)
        y = distance * math.sin(angle_rad)

        position = {
            "mac": mac,
            "angle": round(angle, 1),
            "distance": round(distance, 2),
            "x": round(x, 2),
            "y": round(y, 2),
            "rssi": rssi,
            "zone": self._get_zone(distance),
            "timestamp": datetime.now().isoformat()
        }

        # Pozisyon geçmişine ekle
        if mac not in self.position_history:
            self.position_history[mac] = []
        self.position_history[mac].append(position)
        # Son 50 kaydı tut
        self.position_history[mac] = self.position_history[mac][-50:]

        # Güncel pozisyonu güncelle
        self.device_positions[mac] = position

        return position

    def _get_zone(self, distance: float) -> str:
        """Mesafeye göre bölge belirle."""
        if distance < 2:
            return "Çok Yakın"
        elif distance < 5:
            return "Yakın"
        elif distance < 10:
            return "Orta"
        elif distance < 20:
            return "Uzak"
        else:
            return "Çok Uzak"

    def estimate_all_positions(self, devices: Dict[str, dict]) -> Dict[str, dict]:
        """
        Tüm cihazların konumlarını hesapla.
        
        Args:
            devices: MAC -> cihaz bilgisi sözlüğü
            
        Returns:
            MAC -> konum bilgisi sözlüğü
        """
        total = len(devices)
        positions = {}

        for idx, (mac, device) in enumerate(devices.items()):
            rssi = device.get("rssi", -70)
            position = self.calculate_position_polar(mac, rssi, idx, total)

            # Cihaz bilgilerini konuma ekle
            position["device_type"] = device.get("device_type", "Bilinmeyen")
            position["is_human"] = device.get("is_human_carried", False)
            position["vendor"] = device.get("vendor", "Bilinmeyen")
            position["ip"] = device.get("ip", "")
            position["hostname"] = device.get("hostname", "")

            positions[mac] = position

        return positions

    def get_movement_analysis(self, mac: str) -> dict:
        """
        Cihazın hareket analizini yap.
        RSSI değişimlerinden hareket yönü ve hızı tahmin et.
        
        Args:
            mac: Cihaz MAC adresi
            
        Returns:
            Hareket analizi bilgisi
        """
        history = self.position_history.get(mac, [])

        if len(history) < 2:
            return {
                "is_moving": False,
                "direction": "Sabit",
                "speed_estimate": 0.0,
                "trend": "stable"
            }

        # Son birkaç ölçümü karşılaştır
        recent = history[-5:]
        distances = [p["distance"] for p in recent]

        # Mesafe değişim trendi
        if len(distances) >= 2:
            avg_change = sum(
                distances[i] - distances[i-1] 
                for i in range(1, len(distances))
            ) / (len(distances) - 1)
        else:
            avg_change = 0

        # Hareket yönü
        if avg_change > 0.5:
            direction = "Uzaklaşıyor"
            trend = "moving_away"
        elif avg_change < -0.5:
            direction = "Yaklaşıyor"
            trend = "approaching"
        else:
            direction = "Sabit"
            trend = "stable"

        # Hareket var mı?
        distance_variance = max(distances) - min(distances) if distances else 0
        is_moving = distance_variance > 1.0

        # Hız tahmini (çok kaba)
        speed = abs(avg_change) * 0.5  # m/s yaklaşık

        return {
            "is_moving": is_moving,
            "direction": direction,
            "speed_estimate": round(speed, 2),
            "trend": trend,
            "distance_variance": round(distance_variance, 2),
            "avg_distance_change": round(avg_change, 2)
        }

    def get_heatmap_data(self) -> List[dict]:
        """
        Isı haritası için veri üret.
        Cihaz yoğunluğuna göre bölgelerin ısı değerlerini hesapla.
        
        Returns:
            Isı haritası veri noktaları
        """
        heatmap = []
        grid_size = 20  # 20x20 grid

        for i in range(grid_size):
            for j in range(grid_size):
                x = (i / grid_size) * self.room_width - self.room_width / 2
                y = (j / grid_size) * self.room_height - self.room_height / 2
                intensity = 0

                for mac, pos in self.device_positions.items():
                    dx = x - pos.get("x", 0)
                    dy = y - pos.get("y", 0)
                    dist = math.sqrt(dx*dx + dy*dy)
                    # Gaussian dağılım ile yoğunluk
                    intensity += math.exp(-(dist*dist) / 10.0)

                if intensity > 0.1:
                    heatmap.append({
                        "x": round(x, 1),
                        "y": round(y, 1),
                        "intensity": round(min(intensity, 1.0), 3)
                    })

        return heatmap

    def get_floor_plan_positions(self) -> List[dict]:
        """
        Kat planı görünümü için normalize edilmiş pozisyonlar.
        X, Y koordinatlarını 0-100 aralığına normalize eder.
        
        Returns:
            Normalize edilmiş pozisyon listesi
        """
        positions = []
        max_distance = max(
            (pos.get("distance", 1) for pos in self.device_positions.values()),
            default=10
        )

        for mac, pos in self.device_positions.items():
            # Polar -> Normalize kartezyen
            angle_rad = math.radians(pos.get("angle", 0))
            norm_dist = pos.get("distance", 5) / max_distance

            # 0-100 aralığına normalize et (merkez 50,50)
            nx = 50 + (norm_dist * 40 * math.cos(angle_rad))
            ny = 50 + (norm_dist * 40 * math.sin(angle_rad))

            positions.append({
                "mac": mac,
                "x_percent": round(max(5, min(95, nx)), 1),
                "y_percent": round(max(5, min(95, ny)), 1),
                "device_type": pos.get("device_type", "Bilinmeyen"),
                "is_human": pos.get("is_human", False),
                "distance": pos.get("distance", 0),
                "zone": pos.get("zone", "Bilinmeyen"),
                "ip": pos.get("ip", ""),
                "vendor": pos.get("vendor", ""),
            })

        return positions

    def get_statistics(self) -> dict:
        """Konum istatistikleri."""
        positions = list(self.device_positions.values())
        
        if not positions:
            return {
                "total_tracked": 0,
                "avg_distance": 0,
                "closest_device": None,
                "farthest_device": None,
                "zone_distribution": {}
            }

        distances = [p.get("distance", 0) for p in positions]
        zones = [p.get("zone", "Bilinmeyen") for p in positions]

        zone_dist = {}
        for z in zones:
            zone_dist[z] = zone_dist.get(z, 0) + 1

        closest = min(positions, key=lambda p: p.get("distance", 999))
        farthest = max(positions, key=lambda p: p.get("distance", 0))

        return {
            "total_tracked": len(positions),
            "avg_distance": round(sum(distances) / len(distances), 2),
            "closest_device": {
                "mac": closest.get("mac"),
                "distance": closest.get("distance"),
                "zone": closest.get("zone")
            },
            "farthest_device": {
                "mac": farthest.get("mac"),
                "distance": farthest.get("distance"),
                "zone": farthest.get("zone")
            },
            "zone_distribution": zone_dist
        }
