/*
 * ESP32 CSI Collector Firmware
 * WiFi Radar Projesi - Gerçek CSI Veri Toplama
 * 
 * Bu firmware ESP32-WROOM'u CSI alıcısına dönüştürür.
 * Modemden gelen WiFi paketlerinin CSI verisini toplar
 * ve Serial port üzerinden bilgisayara gönderir.
 * 
 * KURULUM:
 * 1. Arduino IDE'de ESP32 board ekle
 * 2. Board: "ESP32 Dev Module" seç
 * 3. Upload Speed: 921600
 * 4. Bu dosyayı yükle
 * 5. Serial Monitor: 115200 baud
 * 
 * ÇIKTI FORMATI (her satır):
 * CSI,<timestamp>,<mac>,<rssi>,<noise>,<channel>,<bw>,<n_sub>,<n_rx>,<n_tx>,<seq>,<amp1>,<amp2>,...,<phase1>,<phase2>,...
 */

#include <WiFi.h>
#include <esp_wifi.h>
#include <esp_wifi_types.h>
#include <string.h>

// ═══════ AYARLAR ═══════
// Modem bilgilerin (değiştir!)
#define WIFI_SSID "SUPERONLINE_Wi-Fi_8453"
#define WIFI_PASS "SIFRE_BURAYA"  // Modem şifreni yaz

// CSI ayarları
#define CSI_CHANNEL 6        // 2.4GHz kanal (1-13 arası dene)
#define CSI_SEND_RATE_MS 20  // 20ms = ~50 paket/sn

// Serial hızı
#define SERIAL_BAUD 115200

// ═══════ GLOBAL ═══════
static uint32_t packet_count = 0;
static uint32_t error_count = 0;
static bool wifi_connected = false;

// ═══════ CSI CALLBACK ═══════
void wifi_csi_callback(void *ctx, wifi_csi_info_t *info) {
    if (!info || !info->buf) {
        error_count++;
        return;
    }
    
    packet_count++;
    
    // Timestamp (milisaniye)
    uint32_t ts = millis();
    
    // MAC adresi (transmitter)
    char mac_str[18];
    snprintf(mac_str, sizeof(mac_str), "%02x:%02x:%02x:%02x:%02x:%02x",
             info->mac[0], info->mac[1], info->mac[2],
             info->mac[3], info->mac[4], info->mac[5]);
    
    // Temel bilgiler
    int8_t rssi = info->rx_ctrl.rssi;
    int8_t noise = info->rx_ctrl.noise_floor;
    uint8_t channel = info->rx_ctrl.channel;
    uint8_t bw = info->rx_ctrl.cwb;  // 0=20MHz, 1=40MHz
    uint16_t len = info->len;
    uint8_t n_sub = len / 2;  // Her subcarrier = 2 byte (I + Q)
    uint8_t rx_ant = info->rx_ctrl.ant;
    uint8_t sig_mode = info->rx_ctrl.sig_mode;  // 0=non-HT, 1=HT, 3=VHT
    uint16_t seq = info->rx_ctrl.sig_len;
    
    // Subcarrier sayısını sınırla
    if (n_sub > 128) n_sub = 128;
    if (n_sub < 4) return;  // Geçersiz paket
    
    // Serial çıktı başlat
    Serial.print("CSI,");
    Serial.print(ts);
    Serial.print(",");
    Serial.print(mac_str);
    Serial.print(",");
    Serial.print(rssi);
    Serial.print(",");
    Serial.print(noise);
    Serial.print(",");
    Serial.print(channel);
    Serial.print(",");
    Serial.print(bw == 0 ? 20 : 40);
    Serial.print(",");
    Serial.print(n_sub);
    Serial.print(",");
    Serial.print(rx_ant);
    Serial.print(",");
    Serial.print(sig_mode);
    Serial.print(",");
    Serial.print(seq);
    
    // Amplitude ve Phase hesapla (I/Q → polar)
    // Önce amplitude'ları gönder
    Serial.print(",A");
    int8_t *buf = (int8_t *)info->buf;
    for (int i = 0; i < n_sub && i < 64; i++) {
        int8_t imag = buf[i * 2];
        int8_t real = buf[i * 2 + 1];
        // Amplitude = sqrt(I² + Q²), integer yaklaşım
        uint8_t amp = (uint8_t)sqrt((float)(real*real + imag*imag));
        Serial.print(",");
        Serial.print(amp);
    }
    
    // Sonra phase'leri gönder
    Serial.print(",P");
    for (int i = 0; i < n_sub && i < 64; i++) {
        int8_t imag = buf[i * 2];
        int8_t real = buf[i * 2 + 1];
        // Phase = atan2(Q, I) * 100 (integer olarak)
        int16_t phase = (int16_t)(atan2f((float)imag, (float)real) * 100.0f);
        Serial.print(",");
        Serial.print(phase);
    }
    
    Serial.println();  // Satır sonu
}

// ═══════ CSI BAŞLAT ═══════
void setup_csi() {
    wifi_csi_config_t csi_config = {
        .lltf_en = true,           // L-LTF (52 subcarrier)
        .htltf_en = true,          // HT-LTF (daha fazla subcarrier)
        .stbc_htltf2_en = true,
        .ltf_merge_en = true,
        .channel_filter_en = false,
        .manu_scale = false,
        .shift = false,
    };
    
    esp_wifi_set_csi_config(&csi_config);
    esp_wifi_set_csi_rx_cb(wifi_csi_callback, NULL);
    esp_wifi_set_csi(true);
    
    Serial.println("INFO,CSI_ENABLED");
}

// ═══════ WIFI BAĞLANTI ═══════
void setup_wifi() {
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    
    Serial.print("INFO,CONNECTING,");
    Serial.println(WIFI_SSID);
    
    int retry = 0;
    while (WiFi.status() != WL_CONNECTED && retry < 30) {
        delay(500);
        Serial.print(".");
        retry++;
    }
    
    if (WiFi.status() == WL_CONNECTED) {
        wifi_connected = true;
        Serial.print("\nINFO,CONNECTED,");
        Serial.print(WiFi.localIP().toString());
        Serial.print(",CH:");
        Serial.print(WiFi.channel());
        Serial.print(",RSSI:");
        Serial.println(WiFi.RSSI());
    } else {
        Serial.println("\nINFO,CONNECT_FAILED");
        // Promiscuous modda CSI toplamayı dene
        Serial.println("INFO,TRYING_PROMISCUOUS");
        WiFi.disconnect();
        esp_wifi_set_promiscuous(true);
        esp_wifi_set_channel(CSI_CHANNEL, WIFI_SECOND_CHAN_NONE);
        wifi_connected = false;
    }
}

// ═══════ SETUP ═══════
void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(1000);
    
    Serial.println("INFO,ESP32_CSI_COLLECTOR_V1");
    Serial.println("INFO,INITIALIZING");
    
    // WiFi başlat
    setup_wifi();
    
    // CSI toplama başlat
    setup_csi();
    
    Serial.println("INFO,READY");
    Serial.print("INFO,FREE_HEAP,");
    Serial.println(ESP.getFreeHeap());
}

// ═══════ LOOP ═══════
void loop() {
    // Her 5 saniyede durum raporu
    static uint32_t last_status = 0;
    if (millis() - last_status > 5000) {
        last_status = millis();
        
        Serial.print("STATUS,");
        Serial.print(millis());
        Serial.print(",PKTS:");
        Serial.print(packet_count);
        Serial.print(",ERR:");
        Serial.print(error_count);
        Serial.print(",HEAP:");
        Serial.print(ESP.getFreeHeap());
        Serial.print(",RSSI:");
        Serial.print(WiFi.RSSI());
        Serial.print(",CONNECTED:");
        Serial.println(wifi_connected ? "YES" : "NO");
    }
    
    // WiFi bağlantı kontrolü
    if (wifi_connected && WiFi.status() != WL_CONNECTED) {
        Serial.println("WARN,WIFI_LOST,RECONNECTING");
        WiFi.reconnect();
        delay(5000);
    }
    
    delay(10);  // CPU rahatlatma
}
