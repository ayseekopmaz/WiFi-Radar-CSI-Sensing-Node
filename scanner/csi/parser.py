"""
CSI Frame Parser
ESP32'den gelen serial veriyi yapılandırılmış CSI frame'e dönüştürür.
"""

import math
import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CSIFrame:
    """Tek bir CSI ölçümü."""
    timestamp_ms: int = 0
    received_at: float = 0.0
    mac: str = ""
    rssi: int = -100
    noise: int = -90
    channel: int = 0
    bandwidth_mhz: int = 20
    num_subcarriers: int = 0
    rx_antenna: int = 0
    sig_mode: int = 0
    sequence: int = 0
    amplitude: List[float] = field(default_factory=list)
    phase: List[float] = field(default_factory=list)
    valid: bool = False
    
    @property
    def snr(self) -> float:
        """Signal-to-Noise Ratio (dB)."""
        return self.rssi - self.noise
    
    @property
    def mean_amplitude(self) -> float:
        if not self.amplitude:
            return 0.0
        return sum(self.amplitude) / len(self.amplitude)
    
    @property
    def amplitude_variance(self) -> float:
        if len(self.amplitude) < 2:
            return 0.0
        mean = self.mean_amplitude
        return sum((a - mean)**2 for a in self.amplitude) / len(self.amplitude)


class CSIParser:
    """ESP32 serial çıktısını parse eder."""
    
    def __init__(self):
        self.total_parsed = 0
        self.total_errors = 0
        self.last_frame: Optional[CSIFrame] = None
    
    def parse_line(self, line: str) -> Optional[CSIFrame]:
        """
        Bir serial satırını CSIFrame'e dönüştür.
        
        Format:
        CSI,<ts>,<mac>,<rssi>,<noise>,<ch>,<bw>,<nsub>,<rx>,<sig>,<seq>,A,<amp1>,...,P,<phase1>,...
        """
        line = line.strip()
        if not line.startswith("CSI,"):
            return None
        
        try:
            parts = line.split(",")
            if len(parts) < 12:
                self.total_errors += 1
                return None
            
            frame = CSIFrame()
            frame.received_at = time.time()
            frame.timestamp_ms = int(parts[1])
            frame.mac = parts[2]
            frame.rssi = int(parts[3])
            frame.noise = int(parts[4])
            frame.channel = int(parts[5])
            frame.bandwidth_mhz = int(parts[6])
            frame.num_subcarriers = int(parts[7])
            frame.rx_antenna = int(parts[8])
            frame.sig_mode = int(parts[9])
            frame.sequence = int(parts[10])
            
            # Amplitude ve Phase ayır
            amp_start = None
            phase_start = None
            
            for i, p in enumerate(parts):
                if p == "A":
                    amp_start = i + 1
                elif p == "P":
                    phase_start = i + 1
            
            if amp_start and phase_start:
                frame.amplitude = [float(x) for x in parts[amp_start:phase_start-1] if x]
                frame.phase = [float(x) / 100.0 for x in parts[phase_start:] if x]
                # Phase radian cinsinden geldi (x100 ile gönderilmişti)
            
            frame.num_subcarriers = len(frame.amplitude)
            frame.valid = (frame.num_subcarriers >= 4 and 
                          len(frame.phase) == frame.num_subcarriers)
            
            if frame.valid:
                self.total_parsed += 1
                self.last_frame = frame
            else:
                self.total_errors += 1
            
            return frame if frame.valid else None
            
        except (ValueError, IndexError) as e:
            self.total_errors += 1
            return None
    
    def parse_info(self, line: str) -> Optional[dict]:
        """INFO/STATUS satırlarını parse et."""
        line = line.strip()
        if line.startswith("INFO,"):
            parts = line.split(",")
            return {"type": "info", "data": parts[1:]}
        elif line.startswith("STATUS,"):
            parts = line.split(",")
            status = {"type": "status"}
            for part in parts[1:]:
                if ":" in part:
                    k, v = part.split(":", 1)
                    status[k.lower()] = v
            return status
        elif line.startswith("WARN,"):
            parts = line.split(",")
            return {"type": "warning", "data": parts[1:]}
        return None
    
    def get_stats(self) -> dict:
        return {
            "total_parsed": self.total_parsed,
            "total_errors": self.total_errors,
            "error_rate": round(self.total_errors / max(self.total_parsed + self.total_errors, 1), 3),
        }
