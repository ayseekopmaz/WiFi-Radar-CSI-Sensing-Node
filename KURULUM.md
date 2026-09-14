# WiFi Radar - CSI Sensing Sistemi

Gerçek Wi-Fi CSI (Channel State Information) verisi ile temassız insan/nesne tespiti.

## Donanım Gereksinimleri

| Bileşen | Gerekli | Not |
|---------|---------|-----|
| ESP32-WROOM | ✅ | Dev kit (USB'li) |
| USB kablosu | ✅ | Micro-USB veya USB-C (ESP32'ye göre) |
| WiFi modem | ✅ | Herhangi bir 2.4GHz modem |
| Bilgisayar | ✅ | Windows 10/11, Python 3.8+ |

## Kurulum Adımları

### 1. ESP32 Firmware Yükleme

#### Arduino IDE Kurulumu (tek seferlik)

1. [Arduino IDE](https://www.arduino.cc/en/software) indir ve kur
2. **File → Preferences** → "Additional Board Manager URLs" alanına ekle:
   ```
   https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
   ```
3. **Tools → Board → Board Manager** → "esp32" ara ve kur
4. **Tools → Board** → "ESP32 Dev Module" seç

#### Firmware Yükleme

1. `wifi_radar/esp32_firmware/esp32_csi_collector.ino` dosyasını Arduino IDE'de aç
2. **WIFI_SSID** ve **WIFI_PASS** değerlerini kendi modem bilgilerinle değiştir:
   ```c
   #define WIFI_SSID "SUPERONLINE_Wi-Fi_8453"  // Modem adın
   #define WIFI_PASS "SIFREN"                   // Modem şifren
   ```
3. ESP32'yi USB ile bilgisayara tak
4. **Tools → Port** → ESP32'nin portunu seç (COM3, COM4 vb.)
5. **Upload** butonuna bas (→ ikonu)
6. "Done uploading" mesajını bekle

#### Firmware Test

1. **Tools → Serial Monitor** aç (115200 baud)
2. Şu mesajları görmelisin:
   ```
   INFO,ESP32_CSI_COLLECTOR_V1
   INFO,CONNECTED,192.168.1.xxx,CH:6,RSSI:-55
   INFO,CSI_ENABLED
   INFO,READY
   CSI,12345,bc:7e:c3:25:b8:52,-55,-90,6,20,52,...
   ```
3. `CSI,` ile başlayan satırlar = gerçek CSI verisi ✓

### 2. Python Bağımlılıkları

```bash
cd wifi_radar
pip install -r requirements.txt
```

### 3. Uygulamayı Çalıştır

```bash
python app.py
```

Tarayıcında: **http://localhost:5000**

### 4. Kullanım

1. **"ESP32 Bağlan"** butonuna bas → otomatik port bulur
2. **"Başlat"** → CSI veri akışı başlar
3. **"Kalibre Et"** → Odayı 3 saniye boş bırak (background referansı alır)
4. Artık hareket/varlık tespiti aktif!

## Durum Açıklamaları

| Durum | Anlamı |
|-------|--------|
| NO_SIGNAL | ESP32'den veri gelmiyor |
| CALIBRATING | Boş oda referansı alınıyor |
| EMPTY | Ortam boş, hareket yok |
| POSSIBLE_PRESENCE | Küçük sinyal değişimi - olası varlık |
| PRESENT_STATIC | Kesin: ortamda insan var (hareketsiz) |
| MOVING | Hareket tespit edildi |
| APPROACHING | Hedef yaklaşıyor |
| MOVING_AWAY | Hedef uzaklaşıyor |
| SIGNAL_UNRELIABLE | Sinyal kalitesi düşük |

## Teknik Detaylar

### CSI Pipeline

```
ESP32 (firmware) → Serial USB → Python Parser → Pipeline → Presence Engine → Web UI
```

Pipeline adımları:
1. Hampel outlier filter
2. Amplitude normalizasyon
3. Phase unwrap
4. Linear phase removal (CFO)
5. EMA smoothing
6. Background subtraction
7. Feature extraction (variance, correlation, energy)
8. State machine (hysteresis + min duration)

### Fiziksel Prensipler

- WiFi sinyali ortamda engellere çarpar, yansır, absorbe olur
- İnsan vücudu sinyali ~3-4dB zayıflatır
- Hareket eden insan CSI amplitude/phase dalgalanması yaratır
- 52 subcarrier'ın her biri bağımsız bilgi taşır
- Subcarrier korelasyonu düşerse = ortamda değişiklik var

### Sınırlamalar

- **Tek link** ile kesin 2D konum çıkarılamaz (sadece varlık + yön)
- Kalibrasyon olmadan hassas tespit zor
- Duvar arkası tespit sınırlı (sinyal çok zayıflar)
- Çoklu kişi ayrımı tek link'te güvenilir değil

## Sorun Giderme

| Sorun | Çözüm |
|-------|-------|
| "ESP32 bulunamadı" | USB kabloyu kontrol et, driver yükle (CP2102/CH340) |
| CSI verisi gelmiyor | Serial Monitor'de kontrol et, SSID/şifre doğru mu? |
| "SIGNAL_UNRELIABLE" | ESP32'yi modeme yakınlaştır |
| Yanlış tespit | "Kalibre Et" butonuna bas (oda boşken) |
| Port bulunamıyor | Device Manager'da COM portunu kontrol et |

## Driver Kurulumu (gerekirse)

- **CP2102**: https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers
- **CH340**: https://www.wch-ic.com/downloads/CH341SER_ZIP.html
