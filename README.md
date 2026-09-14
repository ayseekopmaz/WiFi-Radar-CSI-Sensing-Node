# 📡 WiFi Radar & CSI Sensing System

![Platform](https://img.shields.io/badge/Hardware-ESP32%20WROOM-red)
![Backend](https://img.shields.io/badge/Backend-Python%20%7C%20Flask%20%7C%20Socket.IO-blue)
![CSI](https://img.shields.io/badge/Sensing-Wi--Fi%20CSI%20%28I%2FQ%20Polar%29-brightgreen)
![License](https://img.shields.io/badge/License-MIT-orange)

**WiFi Radar**, Wi-Fi sinyallerinin ortamdaki fiziksel nesneler ve insan vücudu sebebiyle kırılması/zayıflamasını (**CSI - Channel State Information**) analiz eden temassız bir varlık ve hareket tespit platformudur. 

Kamera, kızılötesi veya PIR sensör **kullanmadan**; sadece Wi-Fi sinyal dalgalanmalarından faydalanarak ortamdaki insan varlığını, hareket yönünü, solunum aktivitelerini tespit eder ve web arayüzü üzerinden anlık görselleştirir[cite: 1, 2, 8].

---

## 🚀 Öne Çıkan Özellikler

* 🚶 **Temassız İnsan Algılama:** Wi-Fi sinyallerinin insan vücudundan yansımasını işleyerek ortam boş mu, hareketsiz kişi var mı veya hareket halinde mi tespit eder.
* 🫁 **Çoklu Frekans (Multi-Band FFT) Analizi:** Solunum, yavaş yürüme ve hızlı hareket gibi farklı insan aktivitelerini frekans spektrumunda sınıflandırır[cite: 8].
* 🛡️ **Gürültü Filtreleme (Adaptive Kalman Filter):** Sinyal gürültülerini filtreleyerek kararlı ve yumuşak veri akışı sağlar[cite: 7, 8].
* 🌐 **Ağ İçi Cihaz Keşfi & Konumlandırma:** Ağdaki bağlı cihazları ARP/Port taraması ile tespit edip radar/kat planı üzerinde gösterir[cite: 1, 9].
* 💬 **Yerel Ağ Mesajlaşması (UDP/TCP & Chat):** Ağdaki diğer cihazlara UDP Broadcast/Unicast ile doğrudan mesaj veya bildirim gönderir[cite: 1, 10].
* ⚡ **Canlı Web Dashboard:** Flask & Socket.IO entegrasyonu ile milisaniyelik CSI veri akışı ve ısı haritası (Heatmap) sunar[cite: 1, 9].

---
## 🧰 Donanım ve Yazılım Gereksinimleri

### Donanım
* **ESP32-WROOM** (USB arayüzlü geliştirme kartı)
* **2.4GHz Wi-Fi Router / Modem**[cite: 2, 5]
* Micro-USB / Type-C bağlantı kablosu

### Yazılım
* **ESP32 Firmware:** Arduino IDE (ESP-IDF CSI API entegre)[cite: 2, 5]
* **Backend:** Python 3.8+
* **Kütüphaneler:** Flask, Flask-SocketIO, PySerial, NumPy, Psutil[cite: 3]
