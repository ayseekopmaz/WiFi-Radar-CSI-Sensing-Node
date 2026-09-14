"""
CSI Preprocessing Pipeline
Gerçek sinyal işleme: phase sanitization, filtreleme, background removal.

Pipeline sırası:
1. Outlier removal (Hampel filter)
2. Amplitude normalizasyon
3. Phase unwrap
4. Linear phase removal (CFO/SFO temizleme)
5. Background subtraction (boş oda referansı)
6. Band-pass filter (hareket frekansları)
7. Sliding window + feature extraction
"""

import math
import time
from typing import List, Optional, Tuple
from collections import deque
from dataclasses import dataclass, field

from .parser import CSIFrame


@dataclass
class ProcessedFrame:
    """İşlenmiş CSI verisi."""
    timestamp: float = 0.0
    rssi: int = -100
    snr: float = 0.0
    num_subcarriers: int = 0
    # İşlenmiş veriler
    amplitude_clean: List[float] = field(default_factory=list)
    phase_clean: List[float] = field(default_factory=list)
    amplitude_diff: List[float] = field(default_factory=list)  # background'dan fark
    # Özellikler (features)
    mean_amplitude: float = 0.0
    amplitude_variance: float = 0.0
    phase_variance: float = 0.0
    subcarrier_correlation: float = 0.0
    total_energy: float = 0.0
    energy_change: float = 0.0  # önceki frame'den fark
    motion_score: float = 0.0  # 0-1 arası hareket göstergesi
    quality_score: float = 0.0  # 0-1 sinyal kalitesi


class CSIPipeline:
    """
    Gerçek zamanlı CSI sinyal işleme pipeline.
    Her yeni frame geldiğinde işler, özellik çıkarır.
    """
    
    # Fiziksel sabitler
    GUARD_SUBCARRIERS = [0, 1, 2, 3, 27, 28, 29, 30, 31]  # 20MHz için null/guard
    MIN_SNR_DB = 5.0    # Minimum kabul edilebilir SNR
    HAMPEL_WINDOW = 5   # Hampel filter pencere
    HAMPEL_THRESHOLD = 3.0  # Median'dan kaç sigma uzaksa outlier
    
    # Band-pass filter (hareket frekansları)
    # İnsan hareketi: 0.5 - 4 Hz → 50pps'de 1-8 sample
    MOTION_LOW_HZ = 0.3
    MOTION_HIGH_HZ = 5.0
    
    def __init__(self):
        # Geçmiş veriler (sliding window)
        self.amplitude_history: deque = deque(maxlen=200)  # Son 200 frame
        self.phase_history: deque = deque(maxlen=200)
        self.processed_history: deque = deque(maxlen=100)
        
        # Background model (boş oda referansı)
        self.background_amplitude: Optional[List[float]] = None
        self.background_phase: Optional[List[float]] = None
        self.background_samples: int = 0
        self.is_calibrated: bool = False
        self._calibration_buffer: List[List[float]] = []
        
        # Filtre durumları
        self._ema_alpha = 0.3  # Exponential Moving Average katsayısı
        self._ema_amplitude: Optional[List[float]] = None
        self._prev_energy: float = 0.0
        
        # İstatistikler
        self.total_processed = 0
        self.total_dropped = 0
    
    def process(self, frame: CSIFrame) -> Optional[ProcessedFrame]:
        """
        Tek bir CSI frame'i işle.
        
        Returns:
            ProcessedFrame veya None (düşük kalite ise)
        """
        if not frame.valid or frame.num_subcarriers < 4:
            self.total_dropped += 1
            return None
        
        # 1. SNR kontrolü
        snr = frame.snr
        if snr < self.MIN_SNR_DB:
            self.total_dropped += 1
            return None
        
        amp = list(frame.amplitude)
        phase = list(frame.phase)
        n_sub = len(amp)
        
        # 2. Guard subcarrier temizliği
        amp, phase = self._remove_guards(amp, phase, n_sub)
        n_sub = len(amp)
        
        if n_sub < 4:
            self.total_dropped += 1
            return None
        
        # 3. Hampel outlier filter (amplitude)
        amp = self._hampel_filter(amp)
        
        # 4. Amplitude normalizasyon
        amp = self._normalize_amplitude(amp)
        
        # 5. Phase unwrap
        phase = self._phase_unwrap(phase)
        
        # 6. Linear phase removal (CFO kompanzasyonu)
        phase = self._remove_linear_phase(phase)
        
        # 7. EMA smooth
        amp = self._ema_smooth(amp)
        
        # 8. Background subtraction
        amp_diff = self._subtract_background(amp)
        
        # 9. Feature extraction
        result = ProcessedFrame()
        result.timestamp = frame.received_at
        result.rssi = frame.rssi
        result.snr = snr
        result.num_subcarriers = n_sub
        result.amplitude_clean = amp
        result.phase_clean = phase
        result.amplitude_diff = amp_diff
        
        self._extract_features(result, amp, phase, amp_diff)
        
        # Geçmişe ekle
        self.amplitude_history.append(amp)
        self.phase_history.append(phase)
        self.processed_history.append(result)
        self.total_processed += 1
        
        return result
    
    # ═══════ ÖN İŞLEME ADIMLARI ═══════
    
    def _remove_guards(self, amp: List[float], phase: List[float],
                       n: int) -> Tuple[List[float], List[float]]:
        """Guard/null subcarrier'ları kaldır."""
        if n <= 32:
            guards = set(self.GUARD_SUBCARRIERS)
        else:
            # 40MHz/64 subcarrier için genişletilmiş guard
            guards = set(range(6)) | set(range(n-5, n))
        
        clean_amp = [amp[i] for i in range(n) if i not in guards]
        clean_phase = [phase[i] for i in range(n) if i not in guards]
        return clean_amp, clean_phase
    
    def _hampel_filter(self, data: List[float]) -> List[float]:
        """Hampel filter - outlier'ları median ile değiştir."""
        n = len(data)
        result = list(data)
        half_w = self.HAMPEL_WINDOW // 2
        
        for i in range(half_w, n - half_w):
            window = sorted(data[i-half_w:i+half_w+1])
            median = window[len(window)//2]
            # MAD (Median Absolute Deviation)
            mad = sorted([abs(x - median) for x in window])[len(window)//2]
            mad = max(mad, 0.001)  # Sıfıra bölme engelle
            
            if abs(data[i] - median) > self.HAMPEL_THRESHOLD * 1.4826 * mad:
                result[i] = median
        
        return result
    
    def _normalize_amplitude(self, amp: List[float]) -> List[float]:
        """Amplitude'u 0-1 arasına normalize et."""
        max_val = max(amp) if amp else 1.0
        if max_val < 0.001:
            return amp
        return [a / max_val for a in amp]
    
    def _phase_unwrap(self, phase: List[float]) -> List[float]:
        """Phase unwrap - 2π atlamalarını düzelt."""
        if not phase:
            return phase
        
        result = [phase[0]]
        for i in range(1, len(phase)):
            diff = phase[i] - phase[i-1]
            # ±π'den büyük atlamayı düzelt
            while diff > math.pi:
                diff -= 2 * math.pi
            while diff < -math.pi:
                diff += 2 * math.pi
            result.append(result[-1] + diff)
        
        return result
    
    def _remove_linear_phase(self, phase: List[float]) -> List[float]:
        """
        Linear phase bileşenini kaldır (CFO/SFO temizleme).
        Least squares ile doğru fit edip çıkar.
        """
        n = len(phase)
        if n < 3:
            return phase
        
        # Linear regression: phase = a*i + b
        x_mean = (n - 1) / 2.0
        y_mean = sum(phase) / n
        
        num = sum((i - x_mean) * (phase[i] - y_mean) for i in range(n))
        den = sum((i - x_mean)**2 for i in range(n))
        
        if abs(den) < 1e-10:
            return phase
        
        slope = num / den
        intercept = y_mean - slope * x_mean
        
        # Linear bileşeni çıkar
        return [phase[i] - (slope * i + intercept) for i in range(n)]
    
    def _ema_smooth(self, amp: List[float]) -> List[float]:
        """Exponential Moving Average ile yumuşatma."""
        if self._ema_amplitude is None or len(self._ema_amplitude) != len(amp):
            self._ema_amplitude = list(amp)
            return amp
        
        alpha = self._ema_alpha
        result = []
        for i in range(len(amp)):
            smoothed = alpha * amp[i] + (1 - alpha) * self._ema_amplitude[i]
            result.append(smoothed)
            self._ema_amplitude[i] = smoothed
        
        return result
    
    def _subtract_background(self, amp: List[float]) -> List[float]:
        """Background çıkarma (kalibre edilmişse)."""
        if not self.is_calibrated or self.background_amplitude is None:
            return [0.0] * len(amp)
        
        n = min(len(amp), len(self.background_amplitude))
        return [amp[i] - self.background_amplitude[i] for i in range(n)]
    
    # ═══════ ÖZELLİK ÇIKARMA ═══════
    
    def _extract_features(self, result: ProcessedFrame, amp: List[float],
                          phase: List[float], amp_diff: List[float]):
        """İşlenmiş veriden özellikler çıkar."""
        n = len(amp)
        
        # Temel istatistikler
        result.mean_amplitude = sum(amp) / n if n > 0 else 0
        
        mean_a = result.mean_amplitude
        result.amplitude_variance = sum((a - mean_a)**2 for a in amp) / n if n > 1 else 0
        
        if phase:
            mean_p = sum(phase) / len(phase)
            result.phase_variance = sum((p - mean_p)**2 for p in phase) / len(phase)
        
        # Toplam enerji
        result.total_energy = sum(a*a for a in amp) / n
        
        # Enerji değişimi (önceki frame'den)
        result.energy_change = abs(result.total_energy - self._prev_energy)
        self._prev_energy = result.total_energy
        
        # Subcarrier korelasyon değişimi
        if len(self.amplitude_history) >= 2:
            prev_amp = self.amplitude_history[-1]
            m = min(len(amp), len(prev_amp))
            if m >= 4:
                result.subcarrier_correlation = self._correlation(amp[:m], prev_amp[:m])
        
        # Motion score (tüm metriklerden birleşik)
        result.motion_score = self._compute_motion_score(result)
        
        # Sinyal kalitesi
        result.quality_score = self._compute_quality(result)
    
    def _correlation(self, a: List[float], b: List[float]) -> float:
        """İki vektör arası Pearson korelasyon."""
        n = len(a)
        if n < 2:
            return 1.0
        
        mean_a = sum(a) / n
        mean_b = sum(b) / n
        
        num = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
        den_a = math.sqrt(sum((x - mean_a)**2 for x in a))
        den_b = math.sqrt(sum((x - mean_b)**2 for x in b))
        
        if den_a < 1e-10 or den_b < 1e-10:
            return 1.0
        
        return num / (den_a * den_b)
    
    def _compute_motion_score(self, result: ProcessedFrame) -> float:
        """
        Hareket skoru hesapla (0=statik, 1=güçlü hareket).
        Birden fazla göstergeyi birleştirir.
        """
        scores = []
        
        # 1. Amplitude varyansı (normalden sapma)
        if result.amplitude_variance > 0.001:
            scores.append(min(1.0, result.amplitude_variance * 20))
        else:
            scores.append(0.0)
        
        # 2. Enerji değişimi
        if result.energy_change > 0.001:
            scores.append(min(1.0, result.energy_change * 10))
        else:
            scores.append(0.0)
        
        # 3. Subcarrier korelasyon düşüşü (1=sabit, <1=değişim)
        corr = result.subcarrier_correlation
        if corr < 0.99:
            scores.append(min(1.0, (1.0 - corr) * 10))
        else:
            scores.append(0.0)
        
        # 4. Phase varyansı
        if result.phase_variance > 0.01:
            scores.append(min(1.0, result.phase_variance * 5))
        else:
            scores.append(0.0)
        
        # Ağırlıklı birleşim
        if not scores:
            return 0.0
        return sum(scores) / len(scores)
    
    def _compute_quality(self, result: ProcessedFrame) -> float:
        """Sinyal kalitesi (0-1)."""
        quality = 0.5
        
        # SNR katkısı
        if result.snr > 20:
            quality += 0.25
        elif result.snr > 10:
            quality += 0.15
        elif result.snr < 5:
            quality -= 0.3
        
        # Subcarrier sayısı
        if result.num_subcarriers >= 40:
            quality += 0.15
        elif result.num_subcarriers >= 20:
            quality += 0.1
        
        return max(0.0, min(1.0, quality))
    
    # ═══════ KALİBRASYON ═══════
    
    def start_calibration(self):
        """Boş oda kalibrasyonu başlat."""
        self._calibration_buffer = []
        self.is_calibrated = False
        self.background_amplitude = None
        self.background_phase = None
    
    def add_calibration_sample(self, frame: CSIFrame) -> int:
        """Kalibrasyon sample'ı ekle. Toplam sample sayısı döner."""
        if frame.valid:
            self._calibration_buffer.append(list(frame.amplitude))
        return len(self._calibration_buffer)
    
    def finish_calibration(self, min_samples: int = 50) -> bool:
        """
        Kalibrasyonu bitir. Background model oluştur.
        En az 50 sample gerekir (1 saniyelik veri @ 50pps).
        """
        if len(self._calibration_buffer) < min_samples:
            return False
        
        # Ortalama amplitude = background
        n_sub = min(len(buf) for buf in self._calibration_buffer)
        self.background_amplitude = [0.0] * n_sub
        
        for buf in self._calibration_buffer:
            for i in range(n_sub):
                self.background_amplitude[i] += buf[i]
        
        count = len(self._calibration_buffer)
        self.background_amplitude = [x / count for x in self.background_amplitude]
        
        # Normalize
        max_val = max(self.background_amplitude) or 1.0
        self.background_amplitude = [x / max_val for x in self.background_amplitude]
        
        self.background_samples = count
        self.is_calibrated = True
        self._calibration_buffer = []
        
        return True
    
    def get_stats(self) -> dict:
        return {
            "total_processed": self.total_processed,
            "total_dropped": self.total_dropped,
            "drop_rate": round(self.total_dropped / max(self.total_processed + self.total_dropped, 1), 3),
            "is_calibrated": self.is_calibrated,
            "background_samples": self.background_samples,
            "buffer_depth": len(self.amplitude_history),
        }
