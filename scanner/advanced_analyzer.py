"""
WiFi Radar - Gelişmiş Sinyal Analiz Motoru
Kalman Filtre, FFT Analizi, Fresnel Zone, Çoklu AP, Fingerprinting
"""

import math
import time
import subprocess
import re
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import deque


# ====================================================================
# KALMAN FİLTRE - Gürültü Eleme & Yumuşak Sinyal Takibi
# ====================================================================
class KalmanFilter:
    """
    1D Kalman Filtresi - RSSI gürültüsünü filtreler.
    WiFi sinyali doğası gereği çok gürültülü (±5-10dBm).
    Kalman filtre ile gerçek sinyal trendi çıkarılır.
    """
    
    def __init__(self, process_noise=0.5, measurement_noise=3.0, initial_estimate=-70):
        self.q = process_noise       # İşlem gürültüsü (ne kadar hızlı değişebilir)
        self.r = measurement_noise   # Ölçüm gürültüsü (RSSI ne kadar gürültülü)
        self.x = initial_estimate    # Tahmin edilen değer
        self.p = 1.0                 # Tahmin belirsizliği
        self.k = 0.0                 # Kalman kazancı
    
    def update(self, measurement: float) -> float:
        """Yeni ölçüm ile filtreyi güncelle, filtrelenmiş değer döndür."""
        # Tahmin adımı
        self.p += self.q
        
        # Güncelleme adımı
        self.k = self.p / (self.p + self.r)
        self.x += self.k * (measurement - self.x)
        self.p *= (1 - self.k)
        
        return self.x
    
    def get_estimate(self) -> float:
        return self.x
    
    def get_uncertainty(self) -> float:
        return self.p


# ====================================================================
# FFT ANALİZİ - Frekans Tabanlı Hareket Tespiti
# ====================================================================
class FFTAnalyzer:
    """
    Hızlı Fourier Dönüşümü ile RSSI zaman serisinden hareket frekansı çıkarır.
    
    - 0.1-0.5 Hz: Nefes alma (göğüs hareketi sinyal değiştirir)
    - 0.5-2 Hz: Yürüme
    - 2-5 Hz: Koşma / hızlı hareket
    - >5 Hz: Gürültü
    """
    
    BREATH_FREQ_RANGE = (0.1, 0.5)   # Hz - nefes alma
    WALK_FREQ_RANGE = (0.5, 2.0)     # Hz - yürüme
    RUN_FREQ_RANGE = (2.0, 5.0)      # Hz - koşma
    
    def __init__(self, sample_rate: float = 1.0):
        """
        Args:
            sample_rate: Saniyede kaç ölçüm yapılıyor (Hz)
        """
        self.sample_rate = sample_rate
    
    def analyze(self, signal: List[float]) -> dict:
        """
        Sinyal verisine FFT uygula.
        Numpy olmadan basit DFT implementasyonu.
        """
        n = len(signal)
        if n < 8:
            return {"dominant_freq": 0, "activity": "unknown", "power_spectrum": []}
        
        # Ortalamayı çıkar (DC bileşeni temizle)
        mean = sum(signal) / n
        centered = [x - mean for x in signal]
        
        # Hanning pencere uygula (spectral leakage azaltma)
        windowed = [centered[i] * (0.5 - 0.5 * math.cos(2 * math.pi * i / (n-1))) 
                    for i in range(n)]
        
        # DFT hesapla (sadece pozitif frekanslar)
        half_n = n // 2
        magnitudes = []
        freqs = []
        
        for k in range(1, half_n):
            real = sum(windowed[t] * math.cos(2 * math.pi * k * t / n) for t in range(n))
            imag = sum(windowed[t] * math.sin(2 * math.pi * k * t / n) for t in range(n))
            magnitude = math.sqrt(real*real + imag*imag) / n
            freq = k * self.sample_rate / n
            magnitudes.append(magnitude)
            freqs.append(freq)
        
        if not magnitudes:
            return {"dominant_freq": 0, "activity": "static", "power_spectrum": []}
        
        # Dominant frekans
        max_idx = magnitudes.index(max(magnitudes))
        dominant_freq = freqs[max_idx]
        max_magnitude = magnitudes[max_idx]
        
        # Frekans bandlarındaki enerji
        breath_power = self._band_power(freqs, magnitudes, *self.BREATH_FREQ_RANGE)
        walk_power = self._band_power(freqs, magnitudes, *self.WALK_FREQ_RANGE)
        run_power = self._band_power(freqs, magnitudes, *self.RUN_FREQ_RANGE)
        total_power = sum(m*m for m in magnitudes) or 1
        
        # Aktivite sınıflandırma
        activity = "static"
        confidence = 0.0
        
        if max_magnitude < 0.3:
            activity = "static"
            confidence = 0.9
        elif breath_power / total_power > 0.3:
            activity = "breathing"  # Kişi orada ama kımıldamıyor
            confidence = breath_power / total_power
        elif walk_power / total_power > 0.25:
            activity = "walking"
            confidence = walk_power / total_power
        elif run_power / total_power > 0.2:
            activity = "running"
            confidence = run_power / total_power
        else:
            activity = "micro_movement"
            confidence = 0.5
        
        return {
            "dominant_freq": round(dominant_freq, 3),
            "max_magnitude": round(max_magnitude, 3),
            "activity": activity,
            "activity_confidence": round(confidence, 2),
            "breath_power": round(breath_power, 4),
            "walk_power": round(walk_power, 4),
            "run_power": round(run_power, 4),
            "total_power": round(total_power, 4),
            "is_human_present": activity in ("breathing", "walking", "running", "micro_movement") and max_magnitude > 0.3,
            "power_spectrum": [{"freq": round(f, 3), "mag": round(m, 4)} 
                             for f, m in zip(freqs[:20], magnitudes[:20])]
        }
    
    def _band_power(self, freqs: List[float], mags: List[float], 
                    low: float, high: float) -> float:
        """Belirli frekans bandındaki toplam enerji."""
        power = 0.0
        for f, m in zip(freqs, mags):
            if low <= f <= high:
                power += m * m
        return power


# ====================================================================
# FRESNEL ZONE - Fiziksel Engel Modelleme
# ====================================================================
class FresnelZoneCalculator:
    """
    Fresnel Zone hesabı ile sinyal yolundaki engelleri modeller.
    
    WiFi sinyali noktadan noktaya düz gitmez - eliptik bir bölgede yayılır.
    Bu bölgeye giren engeller sinyali zayıflatır.
    """
    
    SPEED_OF_LIGHT = 3e8  # m/s
    WIFI_2G_FREQ = 2.4e9  # Hz
    WIFI_5G_FREQ = 5.0e9  # Hz
    
    @classmethod
    def get_wavelength(cls, freq_hz: float = None) -> float:
        """Dalga boyu hesapla (metre)."""
        freq = freq_hz or cls.WIFI_2G_FREQ
        return cls.SPEED_OF_LIGHT / freq
    
    @classmethod
    def fresnel_radius(cls, distance_m: float, freq_hz: float = None, zone: int = 1) -> float:
        """
        n. Fresnel zone yarıçapını hesapla.
        
        Formül: r_n = sqrt(n * λ * d1 * d2 / (d1 + d2))
        Orta noktada (d1=d2=d/2): r_n = sqrt(n * λ * d / 4)
        
        Args:
            distance_m: Verici-alıcı arası mesafe
            freq_hz: Frekans (varsayılan 2.4GHz)
            zone: Fresnel zone numarası (1. zone en kritik)
        """
        wavelength = cls.get_wavelength(freq_hz)
        if distance_m <= 0:
            return 0.0
        radius = math.sqrt(zone * wavelength * distance_m / 4)
        return radius
    
    @classmethod
    def calculate_path_loss(cls, distance_m: float, freq_hz: float = None,
                           num_walls: int = 0, num_humans: int = 0) -> float:
        """
        Toplam path loss hesapla (dB).
        
        - Free space loss
        - Duvar attenuation (her duvar ~6dB)
        - İnsan vücudu attenuation (her kişi ~3dB)
        """
        freq = freq_hz or cls.WIFI_2G_FREQ
        wavelength = cls.get_wavelength(freq)
        
        if distance_m <= 0:
            return 0.0
        
        # Free Space Path Loss (FSPL)
        fspl = 20 * math.log10(distance_m) + 20 * math.log10(freq) - 147.55
        
        # Duvar kaybı (ITU-R P.1238 modeli)
        wall_loss = num_walls * 6.0  # Her duvar ~6dB
        
        # İnsan vücudu kaybı
        human_loss = num_humans * 3.0  # Her insan ~3dB
        
        return fspl + wall_loss + human_loss
    
    @classmethod
    def estimate_obstructions(cls, measured_rssi: float, distance_m: float,
                             tx_power: float = 20.0) -> dict:
        """
        Ölçülen RSSI ile beklenen RSSI arasındaki farktan engel tahmin et.
        
        Args:
            measured_rssi: Ölçülen sinyal (dBm)
            distance_m: Tahmin edilen mesafe
            tx_power: Verici gücü (dBm, varsayılan 20dBm)
        """
        expected_loss = cls.calculate_path_loss(distance_m)
        expected_rssi = tx_power - expected_loss
        
        deficit = expected_rssi - measured_rssi  # Pozitif = beklenenden zayıf
        
        if deficit < 3:
            return {"walls": 0, "humans": 0, "total_loss": 0, "clear_path": True}
        
        # Engel kombinasyonları tahmin et
        # Önce duvarlarla açıkla, kalanı insanlarla
        est_walls = int(deficit / 6)
        remaining = deficit - (est_walls * 6)
        est_humans = max(0, int(remaining / 3))
        
        return {
            "walls": est_walls,
            "humans": est_humans,
            "extra_loss_db": round(deficit, 1),
            "clear_path": False,
            "fresnel_radius_m": round(cls.fresnel_radius(distance_m), 3),
            "estimated_obstructions": est_walls + est_humans
        }


# ====================================================================
# ÇOKLU AP TARAYICI - Tüm WiFi Ağlarından Veri Toplama
# ====================================================================
class MultiAPScanner:
    """
    Çevredeki tüm WiFi erişim noktalarını tarar.
    Her AP'den gelen sinyal gücü ile triangülasyon ve ortam analizi yapar.
    """
    
    def __init__(self):
        self.known_aps: Dict[str, dict] = {}  # BSSID -> AP bilgisi
        self.ap_history: Dict[str, deque] = {}  # BSSID -> sinyal geçmişi
        self.scan_count = 0
        self.last_scan_time = 0
    
    def scan_wifi_networks(self) -> List[dict]:
        """Windows netsh ile çevredeki tüm WiFi ağlarını tara."""
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
                    ssid = line.split(':', 1)[1].strip()
                    current = {"ssid": ssid}
                    
                elif 'BSSID' in line and ':' in line:
                    bssid_part = line.split(':', 1)[1].strip()
                    # BSSID formatı: xx:xx:xx:xx:xx:xx
                    if len(bssid_part) >= 17:
                        current["bssid"] = bssid_part[:17].lower()
                    
                elif ('Sinyal' in line or 'Signal' in line) and '%' in line:
                    match = re.search(r'(\d+)%', line)
                    if match:
                        percent = int(match.group(1))
                        current["signal_percent"] = percent
                        # Windows % → dBm: dBm = (quality / 2) - 100
                        current["rssi"] = int((percent / 2) - 100)
                
                elif 'Kanal' in line or 'Channel' in line:
                    match = re.search(r':\s*(\d+)', line)
                    if match:
                        current["channel"] = int(match.group(1))
                
                elif ('Radyo' in line or 'Radio' in line) and ':' in line:
                    radio = line.split(':', 1)[1].strip()
                    current["radio_type"] = radio
                    if '5' in radio or 'ac' in radio.lower() or 'ax' in radio.lower():
                        current["band"] = "5GHz"
                    else:
                        current["band"] = "2.4GHz"
            
            if current and 'bssid' in current:
                networks.append(current)
                
        except Exception as e:
            pass
        
        self.scan_count += 1
        self.last_scan_time = time.time()
        
        # AP bilgilerini güncelle
        for net in networks:
            bssid = net.get("bssid", "")
            if bssid:
                self.known_aps[bssid] = net
                
                if bssid not in self.ap_history:
                    self.ap_history[bssid] = deque(maxlen=100)
                self.ap_history[bssid].append({
                    "rssi": net.get("rssi", -100),
                    "time": time.time()
                })
        
        return networks
    
    def get_environment_signature(self) -> List[dict]:
        """
        Ortam parmak izi - tüm AP'lerin anlık RSSI değerleri.
        Bu değer ortamda değişiklik olduğunda (insan girişi) değişir.
        """
        signature = []
        for bssid, ap in self.known_aps.items():
            signature.append({
                "bssid": bssid,
                "ssid": ap.get("ssid", ""),
                "rssi": ap.get("rssi", -100),
                "channel": ap.get("channel", 0),
                "band": ap.get("band", "2.4GHz"),
            })
        return sorted(signature, key=lambda x: x["rssi"], reverse=True)
    
    def detect_environment_change(self) -> dict:
        """
        AP sinyallerindeki değişimden ortam değişikliği tespit et.
        Birisi odaya girdiğinde/çıktığında çoklu AP sinyalleri değişir.
        """
        changes = []
        total_change = 0.0
        
        for bssid, history in self.ap_history.items():
            if len(history) < 5:
                continue
            
            samples = list(history)
            recent = [s["rssi"] for s in samples[-5:]]
            older = [s["rssi"] for s in samples[-10:-5]] if len(samples) >= 10 else recent
            
            recent_avg = sum(recent) / len(recent)
            older_avg = sum(older) / len(older)
            change = abs(recent_avg - older_avg)
            
            if change > 2:
                changes.append({
                    "bssid": bssid,
                    "ssid": self.known_aps.get(bssid, {}).get("ssid", ""),
                    "change_db": round(recent_avg - older_avg, 1),
                    "direction": "stronger" if recent_avg > older_avg else "weaker"
                })
                total_change += change
        
        # Birden fazla AP'de eşzamanlı değişim = insan hareketi
        is_significant = len(changes) >= 2 and total_change > 5
        
        return {
            "is_changed": is_significant,
            "affected_aps": len(changes),
            "total_change_db": round(total_change, 1),
            "changes": changes[:10],
            "interpretation": self._interpret_changes(changes, total_change)
        }
    
    def _interpret_changes(self, changes: List[dict], total_change: float) -> str:
        """Değişimleri yorumla."""
        if not changes:
            return "Ortam sabit"
        
        weaker_count = sum(1 for c in changes if c["direction"] == "weaker")
        stronger_count = sum(1 for c in changes if c["direction"] == "stronger")
        
        if total_change < 3:
            return "Minimal değişim (gürültü)"
        elif weaker_count > stronger_count * 2:
            return "Engel artışı - biri aranıza girmiş olabilir"
        elif stronger_count > weaker_count * 2:
            return "Engel azalması - biri uzaklaşmış olabilir"
        elif len(changes) >= 3:
            return "Çoklu AP etkilendi - ortamda hareket var"
        else:
            return "Küçük ortam değişikliği"


# ====================================================================
# SİNYAL PARMAK İZİ - Fingerprinting
# ====================================================================
class SignalFingerprint:
    """
    Ortamın sinyal parmak izini oluşturur ve değişimleri takip eder.
    Baseline alıp, sapmalardan insan varlığı tespit eder.
    """
    
    def __init__(self):
        self.baseline: Optional[Dict[str, float]] = None  # BSSID -> baseline RSSI
        self.baseline_time: float = 0
        self.current: Dict[str, float] = {}
        self.deviation_history: deque = deque(maxlen=50)
    
    def set_baseline(self, ap_signals: Dict[str, float]):
        """Boş ortam baseline'ı ayarla."""
        self.baseline = ap_signals.copy()
        self.baseline_time = time.time()
    
    def update(self, ap_signals: Dict[str, float]):
        """Güncel sinyalleri güncelle ve sapma hesapla."""
        self.current = ap_signals.copy()
        
        if not self.baseline:
            self.baseline = ap_signals.copy()
            self.baseline_time = time.time()
            return
        
        # Her AP için sapma hesapla
        deviation = self._calculate_deviation()
        self.deviation_history.append({
            "time": time.time(),
            "deviation": deviation,
            "affected_count": sum(1 for d in deviation.values() if abs(d) > 2)
        })
    
    def _calculate_deviation(self) -> Dict[str, float]:
        """Baseline'dan sapma (dB)."""
        deviations = {}
        if not self.baseline:
            return deviations
        
        for bssid, current_rssi in self.current.items():
            if bssid in self.baseline:
                deviations[bssid] = current_rssi - self.baseline[bssid]
        
        return deviations
    
    def get_presence_score(self) -> float:
        """
        İnsan varlığı skoru (0-1).
        Baseline'dan ne kadar sapma varsa o kadar yüksek.
        """
        if not self.baseline or not self.current:
            return 0.0
        
        deviations = self._calculate_deviation()
        if not deviations:
            return 0.0
        
        # RMS sapma
        rms = math.sqrt(sum(d*d for d in deviations.values()) / len(deviations))
        
        # Etkilenen AP sayısı
        affected = sum(1 for d in deviations.values() if abs(d) > 2)
        affected_ratio = affected / max(len(deviations), 1)
        
        # Skor = RMS * etkilenen oran
        score = min(1.0, (rms / 10.0) * (0.5 + affected_ratio))
        
        return round(score, 3)
    
    def get_analysis(self) -> dict:
        """Parmak izi analiz sonucu."""
        deviations = self._calculate_deviation()
        presence_score = self.get_presence_score()
        
        return {
            "has_baseline": self.baseline is not None,
            "baseline_age_s": round(time.time() - self.baseline_time, 1) if self.baseline else 0,
            "ap_count": len(self.current),
            "presence_score": presence_score,
            "affected_aps": sum(1 for d in deviations.values() if abs(d) > 2),
            "max_deviation_db": round(max(abs(d) for d in deviations.values()), 1) if deviations else 0,
            "avg_deviation_db": round(sum(abs(d) for d in deviations.values()) / max(len(deviations), 1), 1),
            "interpretation": self._interpret(presence_score, deviations),
        }
    
    def _interpret(self, score: float, deviations: Dict[str, float]) -> str:
        if score < 0.1:
            return "Ortam boş (baseline ile uyumlu)"
        elif score < 0.3:
            return "Minimal değişim - muhtemelen boş"
        elif score < 0.5:
            return "Orta sapma - olası insan varlığı"
        elif score < 0.7:
            return "Belirgin sapma - yüksek olasılıkla insan var"
        else:
            return "Güçlü sapma - kesinlikle ortamda değişiklik"


# ====================================================================
# GELİŞMİŞ CİHAZ TAKİPÇİSİ
# ====================================================================
class AdvancedDeviceTracker:
    """Her cihaz için Kalman filtre + FFT + Fresnel zone hesabı."""
    
    def __init__(self, mac: str):
        self.mac = mac
        self.kalman = KalmanFilter(process_noise=0.3, measurement_noise=4.0)
        self.fft = FFTAnalyzer(sample_rate=0.2)  # 5sn'de 1 ölçüm
        self.raw_samples: deque = deque(maxlen=300)
        self.filtered_samples: deque = deque(maxlen=300)
        self.timestamps: deque = deque(maxlen=300)
        
        # Sonuçlar
        self.filtered_rssi: float = -70
        self.raw_rssi: float = -70
        self.fft_result: dict = {}
        self.fresnel_result: dict = {}
        self.distance: float = 5.0
        self.is_human: bool = False
        self.human_confidence: float = 0.0
        self.activity: str = "unknown"
    
    def add_measurement(self, rssi: int):
        """Yeni RSSI ölçümü ekle ve tüm analizleri güncelle."""
        now = time.time()
        self.raw_rssi = rssi
        self.raw_samples.append(rssi)
        self.timestamps.append(now)
        
        # Kalman filtre
        self.filtered_rssi = self.kalman.update(rssi)
        self.filtered_samples.append(self.filtered_rssi)
        
        # Mesafe hesabı (filtrelenmiş RSSI ile)
        self.distance = self._rssi_to_distance(self.filtered_rssi)
        
        # Fresnel zone
        self.fresnel_result = FresnelZoneCalculator.estimate_obstructions(
            rssi, self.distance
        )
        
        # FFT analizi (yeterli veri varsa)
        if len(self.filtered_samples) >= 16:
            samples = list(self.filtered_samples)[-64:]
            self.fft_result = self.fft.analyze(samples)
            self.activity = self.fft_result.get("activity", "unknown")
            self.is_human = self.fft_result.get("is_human_present", False)
        
        # İnsan güveni hesapla
        self._update_confidence()
    
    def _update_confidence(self):
        """Tüm verilerden insan güveni hesapla."""
        factors = []
        
        # FFT'den aktivite
        if self.fft_result:
            if self.activity in ("breathing", "micro_movement"):
                factors.append(0.8)
            elif self.activity == "walking":
                factors.append(0.9)
            elif self.activity == "running":
                factors.append(0.95)
            elif self.activity == "static":
                factors.append(0.2)
            else:
                factors.append(0.4)
        
        # Sinyal kararlılığı
        if len(self.filtered_samples) >= 10:
            recent = list(self.filtered_samples)[-10:]
            std = math.sqrt(sum((x - sum(recent)/len(recent))**2 for x in recent) / len(recent))
            if 1.0 < std < 6.0:
                factors.append(0.7)  # İnsan kaynaklı dalgalanma
            elif std > 6.0:
                factors.append(0.5)  # Çok dalgalı - yürüyüş
            else:
                factors.append(0.2)  # Çok sabit - cihaz
        
        # Mesafe faktörü
        if self.distance < 3:
            factors.append(0.6)
        elif self.distance < 6:
            factors.append(0.5)
        else:
            factors.append(0.3)
        
        # Fresnel zone engeli
        if self.fresnel_result.get("humans", 0) > 0:
            factors.append(0.7)
        
        self.human_confidence = sum(factors) / max(len(factors), 1)
    
    def _rssi_to_distance(self, rssi: float) -> float:
        """Filtrelenmiş RSSI → metre."""
        ref_rssi = -40
        n = 2.8  # Kapalı alan
        try:
            d = 10 ** ((ref_rssi - rssi) / (10 * n))
            return max(0.1, min(d, 15.0))
        except:
            return 5.0
    
    def get_full_state(self) -> dict:
        """Cihazın tam analiz durumu."""
        return {
            "mac": self.mac,
            "raw_rssi": self.raw_rssi,
            "filtered_rssi": round(self.filtered_rssi, 1),
            "kalman_uncertainty": round(self.kalman.get_uncertainty(), 3),
            "distance_m": round(self.distance, 2),
            "is_human": self.is_human,
            "human_confidence": round(self.human_confidence, 2),
            "activity": self.activity,
            "fft": self.fft_result,
            "fresnel": self.fresnel_result,
            "sample_count": len(self.raw_samples),
            "signal_history": [round(x, 1) for x in list(self.filtered_samples)[-30:]],
        }


# ====================================================================
# ANA GELİŞMİŞ ANALİZ MOTORU
# ====================================================================
class AdvancedWifiAnalyzer:
    """
    Tüm gelişmiş analiz bileşenlerini birleştiren ana motor.
    """
    
    def __init__(self):
        self.trackers: Dict[str, AdvancedDeviceTracker] = {}
        self.ap_scanner = MultiAPScanner()
        self.fingerprint = SignalFingerprint()
        self.grid_size = 20
        self.area_size = 10.0  # metre
        
        # Haritalar
        self.signal_heatmap = [[0.0]*self.grid_size for _ in range(self.grid_size)]
        self.obstruction_map = [[0.0]*self.grid_size for _ in range(self.grid_size)]
        self.movement_heatmap = [[0.0]*self.grid_size for _ in range(self.grid_size)]
        self.human_heatmap = [[0.0]*self.grid_size for _ in range(self.grid_size)]
        
        # İstatistikler
        self.total_scans = 0
        self.environment_events: deque = deque(maxlen=50)
    
    def process_scan(self, devices: Dict[str, dict]) -> dict:
        """
        Tam analiz döngüsü:
        1. Cihaz sinyallerini işle (Kalman + FFT)
        2. WiFi ağlarını tara (çoklu AP)
        3. Parmak izi güncelle
        4. Haritaları güncelle
        5. Ortam değişikliği tespit et
        """
        self.total_scans += 1
        
        # 1. Her cihazın sinyalini işle
        for mac, device in devices.items():
            rssi = device.get("rssi", -70)
            if mac not in self.trackers:
                self.trackers[mac] = AdvancedDeviceTracker(mac)
            self.trackers[mac].add_measurement(rssi)
        
        # 2. Çevredeki WiFi ağlarını tara
        wifi_networks = self.ap_scanner.scan_wifi_networks()
        
        # 3. Parmak izi güncelle
        ap_signals = {net["bssid"]: net["rssi"] for net in wifi_networks if "bssid" in net}
        self.fingerprint.update(ap_signals)
        
        # 4. Haritaları güncelle
        self._update_all_maps(devices)
        
        # 5. Ortam değişikliği
        env_change = self.ap_scanner.detect_environment_change()
        if env_change["is_changed"]:
            self.environment_events.append({
                "time": datetime.now().isoformat(),
                "event": env_change["interpretation"],
                "severity": env_change["total_change_db"]
            })
        
        return self._build_output(devices, wifi_networks, env_change)
    
    def _update_all_maps(self, devices: Dict[str, dict]):
        """Tüm haritaları güncelle."""
        grid = self.grid_size
        
        # Decay (zamanla eski veri silinir)
        for i in range(grid):
            for j in range(grid):
                self.signal_heatmap[i][j] *= 0.6
                self.obstruction_map[i][j] *= 0.9
                self.movement_heatmap[i][j] *= 0.7
                self.human_heatmap[i][j] *= 0.75
        
        for mac, tracker in self.trackers.items():
            distance = tracker.distance
            angle = self._mac_to_angle(mac)
            gx, gy = self._polar_to_grid(distance, angle)
            
            # Sinyal heatmap
            for i in range(grid):
                for j in range(grid):
                    dx = i - gx
                    dy = j - gy
                    d2 = dx*dx + dy*dy
                    intensity = math.exp(-d2 / 6.0) * (1 - abs(tracker.filtered_rssi)/100)
                    self.signal_heatmap[i][j] += intensity
            
            # Engel haritası (Fresnel bazlı)
            if tracker.fresnel_result and not tracker.fresnel_result.get("clear_path", True):
                extra_loss = tracker.fresnel_result.get("extra_loss_db", 0)
                # Cihaz ile merkez arası yolda engel
                center = grid // 2
                ratio = 0.3 + min(extra_loss / 20.0, 0.5)
                wx = int(center + (gx - center) * ratio)
                wy = int(center + (gy - center) * ratio)
                if 0 <= wx < grid and 0 <= wy < grid:
                    strength = min(1.0, extra_loss / 12.0)
                    self.obstruction_map[wx][wy] = max(self.obstruction_map[wx][wy], strength)
                    # Komşulara yay
                    for di in range(-1, 2):
                        for dj in range(-1, 2):
                            ni, nj = wx+di, wy+dj
                            if 0 <= ni < grid and 0 <= nj < grid:
                                self.obstruction_map[ni][nj] = max(
                                    self.obstruction_map[ni][nj], strength * 0.5)
            
            # Hareket heatmap (FFT bazlı)
            if tracker.activity in ("walking", "running", "micro_movement"):
                for di in range(-2, 3):
                    for dj in range(-2, 3):
                        ni, nj = gx+di, gy+dj
                        if 0 <= ni < grid and 0 <= nj < grid:
                            d2 = di*di + dj*dj
                            self.movement_heatmap[ni][nj] += math.exp(-d2/3.0) * 0.4
            
            # İnsan heatmap
            if tracker.human_confidence > 0.4:
                for di in range(-2, 3):
                    for dj in range(-2, 3):
                        ni, nj = gx+di, gy+dj
                        if 0 <= ni < grid and 0 <= nj < grid:
                            d2 = di*di + dj*dj
                            self.human_heatmap[ni][nj] += math.exp(-d2/2.5) * tracker.human_confidence
        
        # Normalize
        self._normalize_map(self.signal_heatmap)
        self._normalize_map(self.movement_heatmap)
        self._normalize_map(self.human_heatmap)
    
    def _normalize_map(self, hmap):
        """Haritayı 0-1 arasına normalize et."""
        max_val = max(max(row) for row in hmap) or 1.0
        if max_val > 1.0:
            for i in range(len(hmap)):
                for j in range(len(hmap[0])):
                    hmap[i][j] /= max_val
    
    def _mac_to_angle(self, mac: str) -> float:
        """MAC → sabit açı (altın açı dağılımı)."""
        h = sum(ord(c) for c in mac)
        return (h * 137.508) % 360
    
    def _polar_to_grid(self, distance: float, angle_deg: float) -> Tuple[int, int]:
        """Polar → grid hücresi."""
        grid = self.grid_size
        center = grid // 2
        grid_dist = (distance / self.area_size) * grid
        angle_rad = math.radians(angle_deg)
        gx = max(0, min(grid-1, int(center + grid_dist * math.cos(angle_rad))))
        gy = max(0, min(grid-1, int(center + grid_dist * math.sin(angle_rad))))
        return gx, gy
    
    def _build_output(self, devices: Dict[str, dict], wifi_networks: List[dict],
                      env_change: dict) -> dict:
        """Frontend için çıktı oluştur."""
        grid = self.grid_size
        
        # Haritaları düz listeye çevir
        signal_data = []
        obstruction_data = []
        movement_data = []
        human_data = []
        
        for i in range(grid):
            for j in range(grid):
                if self.signal_heatmap[i][j] > 0.03:
                    signal_data.append({"x": i, "y": j, "v": round(self.signal_heatmap[i][j], 3)})
                if self.obstruction_map[i][j] > 0.15:
                    obstruction_data.append({"x": i, "y": j, "v": round(self.obstruction_map[i][j], 3)})
                if self.movement_heatmap[i][j] > 0.08:
                    movement_data.append({"x": i, "y": j, "v": round(self.movement_heatmap[i][j], 3)})
                if self.human_heatmap[i][j] > 0.1:
                    human_data.append({"x": i, "y": j, "v": round(self.human_heatmap[i][j], 3)})
        
        # Cihaz pozisyonları
        device_positions = {}
        for mac, tracker in self.trackers.items():
            state = tracker.get_full_state()
            device = devices.get(mac, {})
            angle = self._mac_to_angle(mac)
            
            device_positions[mac] = {
                "distance": state["distance_m"],
                "angle": round(angle, 1),
                "rssi_raw": state["raw_rssi"],
                "rssi_filtered": state["filtered_rssi"],
                "is_human": state["is_human"],
                "human_confidence": state["human_confidence"],
                "activity": state["activity"],
                "is_moving": state["activity"] in ("walking", "running"),
                "movement": "approaching" if state.get("fft", {}).get("dominant_freq", 0) > 0.5 else "stable",
                "fresnel": state["fresnel"],
                "signal_history": state["signal_history"],
                "ip": device.get("ip", ""),
                "vendor": device.get("vendor", ""),
                "hostname": device.get("hostname", ""),
                "device_type": device.get("device_type", ""),
                "sample_count": state["sample_count"],
            }
        
        # Fingerprint analizi
        fp_analysis = self.fingerprint.get_analysis()
        
        return {
            "signal_heatmap": signal_data,
            "obstruction_map": obstruction_data,
            "movement_heatmap": movement_data,
            "human_heatmap": human_data,
            "device_positions": device_positions,
            "wifi_networks": wifi_networks[:20],
            "environment_change": env_change,
            "fingerprint": fp_analysis,
            "environment_events": list(self.environment_events)[-10:],
            "grid_size": grid,
            "area_size_m": self.area_size,
            "stats": {
                "total_tracked": len(self.trackers),
                "humans_detected": sum(1 for t in self.trackers.values() if t.is_human),
                "moving_count": sum(1 for t in self.trackers.values() if t.activity in ("walking", "running")),
                "breathing_detected": sum(1 for t in self.trackers.values() if t.activity == "breathing"),
                "wifi_networks_visible": len(wifi_networks),
                "presence_score": fp_analysis.get("presence_score", 0),
                "total_scans": self.total_scans,
                "avg_confidence": round(
                    sum(t.human_confidence for t in self.trackers.values()) / max(len(self.trackers), 1), 2
                ),
            }
        }
