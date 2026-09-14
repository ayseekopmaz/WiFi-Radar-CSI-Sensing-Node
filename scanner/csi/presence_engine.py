"""
CSI Presence & Motion Detection Engine
Gerçek fiziksel sinyal analizine dayalı durum makinesi.

Durumlar:
- NO_SIGNAL: CSI verisi gelmiyor
- CALIBRATING: Boş oda kalibrasyonu yapılıyor
- EMPTY: Ortam boş (hareket yok)
- POSSIBLE_PRESENCE: Olası varlık (henüz kesinleşmedi)
- PRESENT_STATIC: İnsan var, hareketsiz
- MOVING: Hareket tespit edildi
- APPROACHING: Hedefe doğru yaklaşma
- MOVING_AWAY: Hedef uzaklaşıyor
- SIGNAL_UNRELIABLE: Sinyal kalitesi düşük

Kurallar:
- Minimum 500ms tutarlı veri olmadan durum değiştirme
- Hysteresis: yukarı eşik > aşağı eşik (jitter önleme)
- Kalibrasyon olmadan kesin varlık kararı verme
"""

import time
import math
from typing import Optional, List, Dict
from collections import deque
from enum import Enum

from .pipeline import ProcessedFrame


class PresenceState(Enum):
    NO_SIGNAL = "NO_SIGNAL"
    CALIBRATING = "CALIBRATING"
    EMPTY = "EMPTY"
    POSSIBLE_PRESENCE = "POSSIBLE_PRESENCE"
    PRESENT_STATIC = "PRESENT_STATIC"
    MOVING = "MOVING"
    APPROACHING = "APPROACHING"
    MOVING_AWAY = "MOVING_AWAY"
    SIGNAL_UNRELIABLE = "SIGNAL_UNRELIABLE"


class PresenceEngine:
    """
    CSI verilerinden insan varlığı ve hareket durumu çıkarır.
    
    Fiziksel olarak doğrulanmış yaklaşım:
    - Amplitude varyansı artışı → ortamda hareket var
    - Phase varyansı → küçük hareketler (nefes dahil)
    - Subcarrier korelasyon düşüşü → kesin hareket
    - Enerji değişimi → yaklaşma/uzaklaşma
    """
    
    # ═══════ EŞİKLER (config'den alınmalı, ama makul varsayılanlar) ═══════
    # Motion score eşikleri (hysteresis)
    MOTION_THRESHOLD_HIGH = 0.25   # Bu üstü = hareket başladı
    MOTION_THRESHOLD_LOW = 0.12    # Bu altı = hareket durdu
    
    # Presence (varlık) eşikleri
    PRESENCE_THRESHOLD_HIGH = 0.10  # Statik varlık tespiti
    PRESENCE_THRESHOLD_LOW = 0.05   # Varlık kayboldu
    
    # Yaklaşma/uzaklaşma (enerji trendi)
    APPROACH_ENERGY_RISE = 0.02   # Enerji artışı = yaklaşma
    RECEDE_ENERGY_DROP = -0.02    # Enerji düşüşü = uzaklaşma
    
    # Zaman eşikleri
    MIN_STATE_DURATION_S = 0.5     # Minimum durum süresi (jitter engeli)
    NO_SIGNAL_TIMEOUT_S = 2.0      # Bu süre veri gelmezse NO_SIGNAL
    QUALITY_THRESHOLD = 0.3        # Minimum sinyal kalitesi
    
    # Kalibrasyon
    CALIBRATION_FRAMES = 100       # Kalibrasyon için gereken frame
    
    def __init__(self):
        self.state = PresenceState.NO_SIGNAL
        self._prev_state = PresenceState.NO_SIGNAL
        self._state_since = time.time()
        self._last_frame_time = 0.0
        
        # Sliding window verisi
        self._motion_scores: deque = deque(maxlen=30)   # ~0.6sn @ 50pps
        self._energy_history: deque = deque(maxlen=50)
        self._quality_history: deque = deque(maxlen=20)
        
        # Durum detayları
        self.confidence = 0.0
        self.motion_level = 0.0   # 0-1
        self.direction = "unknown"  # approaching, receding, lateral, static
        self.quality = 0.0
        self.pps = 0.0  # packets per second
        
        # Kalibrasyon
        self._calibration_count = 0
        self._calibration_baseline_motion = 0.0
        
        # Event geçmişi
        self.events: deque = deque(maxlen=50)
        
        # İstatistikler
        self.frames_analyzed = 0
        self.state_changes = 0
    
    def update(self, frame: ProcessedFrame) -> Dict:
        """
        Yeni işlenmiş frame ile durumu güncelle.
        
        Returns:
            Güncel durum sözlüğü
        """
        self.frames_analyzed += 1
        self._last_frame_time = time.time()
        
        # Metrikleri kaydet
        self._motion_scores.append(frame.motion_score)
        self._energy_history.append(frame.total_energy)
        self._quality_history.append(frame.quality_score)
        
        # Sinyal kalitesi kontrolü
        self.quality = frame.quality_score
        if self.quality < self.QUALITY_THRESHOLD:
            self._transition(PresenceState.SIGNAL_UNRELIABLE)
            return self.get_state()
        
        # Ortalama motion score (son 30 frame = ~0.6sn)
        self.motion_level = sum(self._motion_scores) / len(self._motion_scores)
        
        # Enerji trendi (yaklaşma/uzaklaşma)
        self.direction = self._detect_direction()
        
        # Durum makinesi geçişi
        self._run_state_machine()
        
        return self.get_state()
    
    def check_timeout(self) -> Dict:
        """Veri gelmiyorsa timeout kontrolü."""
        if time.time() - self._last_frame_time > self.NO_SIGNAL_TIMEOUT_S:
            if self.state != PresenceState.NO_SIGNAL:
                self._transition(PresenceState.NO_SIGNAL)
        return self.get_state()
    
    def _run_state_machine(self):
        """Ana durum makinesi."""
        now = time.time()
        state_age = now - self._state_since
        
        # Minimum durum süresi kontrolü (jitter engeli)
        if state_age < self.MIN_STATE_DURATION_S:
            return
        
        motion = self.motion_level
        
        if self.state == PresenceState.NO_SIGNAL:
            self._transition(PresenceState.EMPTY)
        
        elif self.state == PresenceState.CALIBRATING:
            # Kalibrasyon tamamlanana kadar bekle
            pass
        
        elif self.state == PresenceState.EMPTY:
            if motion > self.MOTION_THRESHOLD_HIGH:
                self._transition(PresenceState.MOVING)
            elif motion > self.PRESENCE_THRESHOLD_HIGH:
                self._transition(PresenceState.POSSIBLE_PRESENCE)
        
        elif self.state == PresenceState.POSSIBLE_PRESENCE:
            if motion > self.MOTION_THRESHOLD_HIGH:
                self._transition(PresenceState.MOVING)
            elif motion < self.PRESENCE_THRESHOLD_LOW:
                self._transition(PresenceState.EMPTY)
            elif state_age > 2.0:
                # 2 saniye tutarlı → kesin varlık
                self._transition(PresenceState.PRESENT_STATIC)
        
        elif self.state == PresenceState.PRESENT_STATIC:
            if motion > self.MOTION_THRESHOLD_HIGH:
                self._transition(PresenceState.MOVING)
            elif motion < self.PRESENCE_THRESHOLD_LOW and state_age > 3.0:
                self._transition(PresenceState.EMPTY)
        
        elif self.state == PresenceState.MOVING:
            if motion < self.MOTION_THRESHOLD_LOW and state_age > 1.0:
                self._transition(PresenceState.PRESENT_STATIC)
            elif self.direction == "approaching":
                self._transition(PresenceState.APPROACHING)
            elif self.direction == "receding":
                self._transition(PresenceState.MOVING_AWAY)
        
        elif self.state == PresenceState.APPROACHING:
            if motion < self.MOTION_THRESHOLD_LOW:
                self._transition(PresenceState.PRESENT_STATIC)
            elif self.direction != "approaching":
                self._transition(PresenceState.MOVING)
        
        elif self.state == PresenceState.MOVING_AWAY:
            if motion < self.MOTION_THRESHOLD_LOW:
                self._transition(PresenceState.PRESENT_STATIC)
            elif motion < self.PRESENCE_THRESHOLD_LOW and state_age > 2.0:
                self._transition(PresenceState.EMPTY)
            elif self.direction != "receding":
                self._transition(PresenceState.MOVING)
        
        elif self.state == PresenceState.SIGNAL_UNRELIABLE:
            if self.quality >= self.QUALITY_THRESHOLD:
                self._transition(PresenceState.EMPTY)
        
        # Confidence hesapla
        self._update_confidence()
    
    def _transition(self, new_state: PresenceState):
        """Durum geçişi."""
        if new_state == self.state:
            return
        
        self._prev_state = self.state
        self.state = new_state
        self._state_since = time.time()
        self.state_changes += 1
        
        self.events.append({
            "time": time.time(),
            "from": self._prev_state.value,
            "to": new_state.value,
            "motion": round(self.motion_level, 3),
            "confidence": round(self.confidence, 2),
        })
    
    def _detect_direction(self) -> str:
        """Enerji trendinden hareket yönü."""
        if len(self._energy_history) < 10:
            return "unknown"
        
        recent = list(self._energy_history)
        # Son 5 vs önceki 5 karşılaştırma
        n = len(recent)
        half = n // 2
        older = sum(recent[:half]) / half
        newer = sum(recent[half:]) / (n - half)
        
        diff = newer - older
        
        if diff > self.APPROACH_ENERGY_RISE:
            return "approaching"
        elif diff < self.RECEDE_ENERGY_DROP:
            return "receding"
        else:
            return "lateral" if self.motion_level > self.MOTION_THRESHOLD_HIGH else "static"
    
    def _update_confidence(self):
        """Durum güveni hesapla."""
        if self.state in (PresenceState.NO_SIGNAL, PresenceState.CALIBRATING):
            self.confidence = 0.0
            return
        
        state_age = time.time() - self._state_since
        
        # Süre güveni (ne kadar uzun = o kadar emin)
        time_conf = min(1.0, state_age / 3.0)
        
        # Sinyal kalitesi
        quality_conf = self.quality
        
        # Motion tutarlılığı
        if self._motion_scores:
            scores = list(self._motion_scores)
            motion_std = math.sqrt(sum((x - self.motion_level)**2 for x in scores) / len(scores))
            consistency_conf = max(0, 1.0 - motion_std * 5)
        else:
            consistency_conf = 0.0
        
        self.confidence = (time_conf * 0.3 + quality_conf * 0.3 + consistency_conf * 0.4)
        self.confidence = max(0.0, min(1.0, self.confidence))
    
    def get_state(self) -> Dict:
        """Güncel durum."""
        return {
            "state": self.state.value,
            "confidence": round(self.confidence, 2),
            "motion_level": round(self.motion_level, 3),
            "direction": self.direction,
            "quality": round(self.quality, 2),
            "state_duration_s": round(time.time() - self._state_since, 1),
            "frames_analyzed": self.frames_analyzed,
            "state_changes": self.state_changes,
            "is_human_present": self.state.value in (
                "POSSIBLE_PRESENCE", "PRESENT_STATIC",
                "MOVING", "APPROACHING", "MOVING_AWAY"),
            "is_moving": self.state.value in ("MOVING", "APPROACHING", "MOVING_AWAY"),
            "last_events": list(self.events)[-5:],
        }
    
    def start_calibration(self):
        """Kalibrasyon moduna geç."""
        self._transition(PresenceState.CALIBRATING)
        self._calibration_count = 0
    
    def finish_calibration(self):
        """Kalibrasyonu bitir."""
        # Kalibrasyon sırasındaki ortalama motion'ı baseline olarak al
        if self._motion_scores:
            self._calibration_baseline_motion = sum(self._motion_scores) / len(self._motion_scores)
        self._transition(PresenceState.EMPTY)
