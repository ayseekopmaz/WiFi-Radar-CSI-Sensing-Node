"""
WiFi Radar - WiFi Tarama ve Cihaz Tespit Modülü
Scapy ve sistem araçları kullanarak ağdaki cihazları tespit eder.
"""

import subprocess
import re
import socket
import struct
import time
import threading
from datetime import datetime
from typing import Dict, List, Optional

try:
    from scapy.all import ARP, Ether, srp, sniff, Dot11, Dot11Beacon, Dot11ProbeReq
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False

try:
    import nmap
    NMAP_AVAILABLE = True
except ImportError:
    NMAP_AVAILABLE = False


class WiFiScanner:
    """WiFi ağını tarayarak cihaz ve insan tespiti yapar."""

    # Bilinen üretici MAC prefix'leri (OUI - ilk 3 byte)
    KNOWN_VENDORS = {
        "00:50:F2": "Microsoft",
        "00:1A:2B": "Ayecom",
        "00:1B:63": "Apple",
        "00:03:93": "Apple",
        "00:05:02": "Apple",
        "00:0A:95": "Apple",
        "00:0D:93": "Apple",
        "00:11:24": "Apple",
        "00:14:51": "Apple",
        "00:16:CB": "Apple",
        "00:17:F2": "Apple",
        "00:19:E3": "Apple",
        "00:1C:B3": "Apple",
        "00:1D:4F": "Apple",
        "00:1E:52": "Apple",
        "00:1F:5B": "Apple",
        "00:1F:F3": "Apple",
        "00:21:E9": "Apple",
        "00:22:41": "Apple",
        "00:23:12": "Apple",
        "00:23:32": "Apple",
        "00:23:6C": "Apple",
        "00:23:DF": "Apple",
        "00:24:36": "Apple",
        "00:25:00": "Apple",
        "00:25:BC": "Apple",
        "00:26:08": "Apple",
        "00:26:4A": "Apple",
        "00:26:B0": "Apple",
        "00:26:BB": "Apple",
        "3C:5A:B4": "Google",
        "54:60:09": "Google",
        "F4:F5:D8": "Google",
        "A4:77:33": "Google",
        "00:1A:11": "Google",
        "94:EB:2C": "Google",
        "30:FD:38": "Google",
        "F8:8F:CA": "Google",
        "00:50:56": "VMware",
        "00:0C:29": "VMware",
        "00:15:5D": "Microsoft Hyper-V",
        "08:00:27": "VirtualBox",
        "B8:27:EB": "Raspberry Pi",
        "DC:A6:32": "Raspberry Pi",
        "E4:5F:01": "Raspberry Pi",
        "28:CD:C1": "Raspberry Pi",
        "00:0E:C6": "ASUS",
        "00:11:2F": "ASUS",
        "00:15:F2": "ASUS",
        "00:1A:92": "ASUS",
        "00:1E:8C": "ASUS",
        "00:22:15": "ASUS",
        "00:23:54": "ASUS",
        "00:24:8C": "ASUS",
        "00:26:18": "ASUS",
        "14:DA:E9": "ASUS",
        "AC:22:05": "ASUS",
        "D8:50:E6": "ASUS",
        "F0:79:59": "ASUS",
        "00:18:E7": "Samsung",
        "00:1A:8A": "Samsung",
        "00:1B:98": "Samsung",
        "00:1C:43": "Samsung",
        "00:1D:25": "Samsung",
        "00:1E:E1": "Samsung",
        "00:1E:E2": "Samsung",
        "00:21:19": "Samsung",
        "00:21:D1": "Samsung",
        "00:21:D2": "Samsung",
        "00:23:39": "Samsung",
        "00:23:3A": "Samsung",
        "00:23:99": "Samsung",
        "00:23:D6": "Samsung",
        "00:23:D7": "Samsung",
        "00:24:54": "Samsung",
        "00:24:90": "Samsung",
        "00:24:91": "Samsung",
        "00:25:66": "Samsung",
        "00:25:67": "Samsung",
        "00:26:37": "Samsung",
        "00:E0:4C": "Realtek",
        "00:0B:6B": "Huawei",
        "00:E0:FC": "Huawei",
        "00:18:82": "Huawei",
        "00:1E:10": "Huawei",
        "00:25:68": "Huawei",
        "00:25:9E": "Huawei",
        "00:46:4B": "Huawei",
        "04:02:1F": "Huawei",
        "04:25:C5": "Huawei",
        "04:BD:70": "Huawei",
        "04:C0:6F": "Huawei",
        "04:F9:38": "Huawei",
    }

    # Cihaz tipi tahmini için port-servis eşleşmeleri
    DEVICE_TYPE_PORTS = {
        "Akıllı Telefon": [5353, 62078],  # mDNS, Apple services
        "Bilgisayar": [22, 135, 139, 445, 3389],  # SSH, Windows services
        "IoT Cihaz": [1883, 8883, 5683],  # MQTT, CoAP
        "Yazıcı": [9100, 515, 631],  # Printing protocols
        "TV/Medya": [8008, 8443, 9080],  # Chromecast, DLNA
        "Router/AP": [80, 443, 23, 53],  # HTTP, HTTPS, Telnet, DNS
    }

    def __init__(self):
        self.devices: Dict[str, dict] = {}
        self.scan_history: List[dict] = []
        self.is_scanning = False
        self._scan_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.local_ip = self._get_local_ip()
        self.network_prefix = self._get_network_prefix()

    def _get_local_ip(self) -> str:
        """Yerel IP adresini al."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "192.168.1.1"

    def _get_network_prefix(self) -> str:
        """Ağ prefix'ini hesapla (örn: 192.168.1)."""
        parts = self.local_ip.split(".")
        return f"{parts[0]}.{parts[1]}.{parts[2]}"

    def get_vendor(self, mac: str) -> str:
        """MAC adresinden üretici bilgisini al."""
        mac_prefix = mac.upper()[:8]
        return self.KNOWN_VENDORS.get(mac_prefix, "Bilinmeyen")

    def estimate_distance(self, rssi: int, frequency: int = 2400) -> float:
        """
        RSSI değerinden mesafe tahmini yapar (metre).
        Free Space Path Loss (FSPL) formülü kullanılır.
        """
        if rssi >= 0:
            return 0.0
        # FSPL formülü ile mesafe hesaplama
        # d = 10 ^ ((27.55 - (20 * log10(f)) + |RSSI|) / 20)
        import math
        try:
            exp = (27.55 - (20 * math.log10(frequency)) + abs(rssi)) / 20.0
            distance = 10 ** exp
            return round(distance, 2)
        except (ValueError, OverflowError):
            return 999.0

    def classify_device(self, mac: str, vendor: str, hostname: str, open_ports: List[int]) -> dict:
        """Cihaz tipini sınıflandır."""
        device_type = "Bilinmeyen Cihaz"
        is_human_carried = False

        # Port bazlı sınıflandırma
        for dtype, ports in self.DEVICE_TYPE_PORTS.items():
            if any(p in open_ports for p in ports):
                device_type = dtype
                break

        # Vendor bazlı ek sınıflandırma
        phone_vendors = ["Apple", "Samsung", "Huawei", "Google", "Xiaomi", "OnePlus", "OPPO", "Vivo"]
        computer_vendors = ["Microsoft", "ASUS", "Dell", "HP", "Lenovo", "Acer"]

        if vendor in phone_vendors:
            device_type = "Akıllı Telefon"
            is_human_carried = True
        elif vendor in computer_vendors:
            device_type = "Bilgisayar"
            is_human_carried = True

        # Hostname bazlı ipuçları
        hostname_lower = hostname.lower() if hostname else ""
        if any(k in hostname_lower for k in ["iphone", "android", "galaxy", "pixel", "huawei"]):
            device_type = "Akıllı Telefon"
            is_human_carried = True
        elif any(k in hostname_lower for k in ["laptop", "desktop", "pc", "macbook", "notebook"]):
            device_type = "Bilgisayar"
            is_human_carried = True
        elif any(k in hostname_lower for k in ["tv", "chromecast", "firestick", "roku"]):
            device_type = "TV/Medya"
            is_human_carried = False
        elif any(k in hostname_lower for k in ["printer", "canon", "epson", "hp-"]):
            device_type = "Yazıcı"
            is_human_carried = False

        return {
            "device_type": device_type,
            "is_human_carried": is_human_carried
        }

    def arp_scan(self, network_range: Optional[str] = None) -> List[dict]:
        """ARP taraması ile ağdaki aktif cihazları tespit et."""
        if network_range is None:
            network_range = f"{self.network_prefix}.0/24"

        devices_found = []

        if SCAPY_AVAILABLE:
            try:
                arp = ARP(pdst=network_range)
                ether = Ether(dst="ff:ff:ff:ff:ff:ff")
                packet = ether / arp

                result = srp(packet, timeout=3, verbose=0)[0]

                for sent, received in result:
                    mac = received.hwsrc
                    ip = received.psrc
                    vendor = self.get_vendor(mac)
                    hostname = self._resolve_hostname(ip)

                    device_info = {
                        "mac": mac,
                        "ip": ip,
                        "vendor": vendor,
                        "hostname": hostname,
                        "rssi": self._estimate_rssi(ip),
                        "last_seen": datetime.now().isoformat(),
                        "first_seen": datetime.now().isoformat(),
                    }
                    devices_found.append(device_info)
            except Exception as e:
                print(f"Scapy ARP tarama hatası: {e}")
                devices_found = self._fallback_arp_scan(network_range)
        else:
            devices_found = self._fallback_arp_scan(network_range)

        return devices_found

    def _fallback_arp_scan(self, network_range: str) -> List[dict]:
        """Scapy olmadan ARP tablosundan cihaz bilgisi al."""
        devices = []
        try:
            # Önce ağı ping ile tara
            prefix = network_range.split("/")[0].rsplit(".", 1)[0]
            # Windows için arp -a komutu
            result = subprocess.run(
                ["arp", "-a"],
                capture_output=True, text=True, timeout=10
            )

            # ARP tablosunu parse et
            lines = result.stdout.split("\n")
            for line in lines:
                # Windows ARP formatı: IP adresi     MAC adresi     Tür
                match = re.search(
                    r'(\d+\.\d+\.\d+\.\d+)\s+([\w-]+(?::[\w-]+){5}|[\w-]+(?:-[\w-]+){5})',
                    line
                )
                if match:
                    ip = match.group(1)
                    mac = match.group(2).replace("-", ":").lower()

                    if mac == "ff:ff:ff:ff:ff:ff" or ip.endswith(".255"):
                        continue

                    vendor = self.get_vendor(mac)
                    hostname = self._resolve_hostname(ip)

                    devices.append({
                        "mac": mac,
                        "ip": ip,
                        "vendor": vendor,
                        "hostname": hostname,
                        "rssi": self._estimate_rssi(ip),
                        "last_seen": datetime.now().isoformat(),
                        "first_seen": datetime.now().isoformat(),
                    })
        except Exception as e:
            print(f"Fallback ARP tarama hatası: {e}")

        return devices

    def _resolve_hostname(self, ip: str) -> str:
        """IP adresinden hostname çözümle."""
        try:
            hostname = socket.gethostbyaddr(ip)[0]
            return hostname
        except (socket.herror, socket.gaierror, OSError):
            return ""

    def _estimate_rssi(self, ip: str) -> int:
        """
        Ping yanıt süresinden RSSI tahmini yap.
        TURBO: Daha kısa timeout, daha hızlı.
        """
        try:
            result = subprocess.run(
                ["ping", "-n", "1", "-w", "500", ip],  # 1000 → 500ms
                capture_output=True, text=True, timeout=3  # 5 → 3sn
            )
            # Yanıt süresinden RSSI tahmini
            time_match = re.search(r'time[=<](\d+)', result.stdout)
            if time_match:
                ping_time = int(time_match.group(1))
                # Ping süresini yaklaşık RSSI'ye çevir
                if ping_time < 5:
                    return -30  # Çok yakın
                elif ping_time < 20:
                    return -50  # Yakın
                elif ping_time < 50:
                    return -65  # Orta
                elif ping_time < 100:
                    return -75  # Uzak
                else:
                    return -85  # Çok uzak
            return -70  # Varsayılan
        except Exception:
            return -70

    def scan_ports(self, ip: str, ports: Optional[List[int]] = None) -> List[int]:
        """Hedef IP'de açık portları tara."""
        if ports is None:
            ports = [22, 23, 53, 80, 135, 139, 443, 445, 515, 631,
                     1883, 3389, 5353, 5683, 8008, 8080, 8443, 8883,
                     9080, 9100, 62078]

        open_ports = []

        if NMAP_AVAILABLE:
            try:
                nm = nmap.PortScanner()
                port_str = ",".join(str(p) for p in ports)
                nm.scan(ip, port_str, arguments="-sT --host-timeout 5s")
                if ip in nm.all_hosts():
                    for proto in nm[ip].all_protocols():
                        for port in nm[ip][proto].keys():
                            if nm[ip][proto][port]["state"] == "open":
                                open_ports.append(port)
            except Exception:
                open_ports = self._fallback_port_scan(ip, ports)
        else:
            open_ports = self._fallback_port_scan(ip, ports)

        return open_ports

    def _fallback_port_scan(self, ip: str, ports: List[int]) -> List[int]:
        """Socket ile basit port tarama - HIZLI."""
        open_ports = []
        for port in ports:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.2)  # 0.5 → 0.2sn
                result = sock.connect_ex((ip, port))
                if result == 0:
                    open_ports.append(port)
                sock.close()
            except Exception:
                pass
        return open_ports

    def full_scan(self) -> Dict[str, dict]:
        """Tam ağ taraması yap."""
        self.is_scanning = True
        discovered = self.arp_scan()

        for device in discovered:
            mac = device["mac"]
            ip = device["ip"]

            # Port taraması
            open_ports = self.scan_ports(ip)
            device["open_ports"] = open_ports

            # Cihaz sınıflandırma
            classification = self.classify_device(
                mac, device["vendor"], device["hostname"], open_ports
            )
            device.update(classification)

            # Mesafe tahmini
            device["distance_m"] = self.estimate_distance(device["rssi"])

            # Mevcut cihaz güncelle veya yeni ekle
            if mac in self.devices:
                self.devices[mac].update(device)
                self.devices[mac]["last_seen"] = datetime.now().isoformat()
            else:
                self.devices[mac] = device

        # Tarama geçmişine ekle
        self.scan_history.append({
            "timestamp": datetime.now().isoformat(),
            "device_count": len(discovered),
            "human_count": sum(1 for d in discovered if d.get("is_human_carried", False))
        })

        self.is_scanning = False
        return self.devices

    def quick_scan(self) -> Dict[str, dict]:
        """Hızlı ARP taraması (port taraması olmadan)."""
        self.is_scanning = True
        discovered = self.arp_scan()

        for device in discovered:
            mac = device["mac"]
            vendor = device["vendor"]

            # Basit sınıflandırma (port taraması olmadan)
            phone_vendors = ["Apple", "Samsung", "Huawei", "Google", "Xiaomi"]
            computer_vendors = ["Microsoft", "ASUS", "Dell", "HP", "Lenovo"]

            if vendor in phone_vendors:
                device["device_type"] = "Akıllı Telefon"
                device["is_human_carried"] = True
            elif vendor in computer_vendors:
                device["device_type"] = "Bilgisayar"
                device["is_human_carried"] = True
            else:
                device["device_type"] = "Bilinmeyen Cihaz"
                device["is_human_carried"] = False

            device["distance_m"] = self.estimate_distance(device["rssi"])
            device["open_ports"] = []

            if mac in self.devices:
                self.devices[mac].update(device)
                self.devices[mac]["last_seen"] = datetime.now().isoformat()
            else:
                self.devices[mac] = device

        self.is_scanning = False
        return self.devices

    def start_continuous_scan(self, interval: int = 10):
        """Sürekli tarama başlat."""
        self._stop_event.clear()
        self._scan_thread = threading.Thread(
            target=self._continuous_scan_loop,
            args=(interval,),
            daemon=True
        )
        self._scan_thread.start()

    def _continuous_scan_loop(self, interval: int):
        """Sürekli tarama döngüsü."""
        while not self._stop_event.is_set():
            self.quick_scan()
            self._stop_event.wait(interval)

    def stop_continuous_scan(self):
        """Sürekli taramayı durdur."""
        self._stop_event.set()
        if self._scan_thread:
            self._scan_thread.join(timeout=5)

    def get_humans(self) -> List[dict]:
        """İnsan taşıdığı tahmin edilen cihazları getir."""
        return [
            device for device in self.devices.values()
            if device.get("is_human_carried", False)
        ]

    def get_device_by_ip(self, ip: str) -> Optional[dict]:
        """IP adresine göre cihaz bilgisi getir."""
        for device in self.devices.values():
            if device["ip"] == ip:
                return device
        return None

    def get_device_by_mac(self, mac: str) -> Optional[dict]:
        """MAC adresine göre cihaz bilgisi getir."""
        return self.devices.get(mac.lower())

    def get_statistics(self) -> dict:
        """Tarama istatistikleri."""
        total = len(self.devices)
        humans = len(self.get_humans())
        return {
            "total_devices": total,
            "human_carried": humans,
            "other_devices": total - humans,
            "scan_count": len(self.scan_history),
            "last_scan": self.scan_history[-1]["timestamp"] if self.scan_history else None,
            "network": f"{self.network_prefix}.0/24",
            "local_ip": self.local_ip,
        }
