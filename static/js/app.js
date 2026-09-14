/**
 * CSI RADAR - Premium Frontend
 * Gerçek zamanlı CSI durum, grafik ve timeline
 */

const socket = io();
let streaming = false;
let lastAmplitude = [];

document.addEventListener('DOMContentLoaded', () => {
    initSocket();
    initUI();
    checkHW();
});

// ═══════ UI INIT ═══════
function initUI() {
    $('btnConnect').addEventListener('click', doConnect);
    $('btnStream').addEventListener('click', toggleStream);
    $('btnCalibrate').addEventListener('click', doCalibrate);
}

// ═══════ DONANIM ═══════
async function checkHW() {
    try {
        const d = await api('/api/csi/detect-hardware');
        if (d.esp32_detected) {
            toast(`ESP32 bulundu: ${d.detected_port}`, 'info');
            $('btnConnect').innerHTML = `<i class="fas fa-plug"></i> ${d.detected_port}`;
        }
    } catch(e) {}
}

async function doConnect() {
    toast('ESP32 aranıyor...', 'info');
    const d = await api('/api/csi/connect', 'POST', {});
    if (d.success) {
        toast(`Bağlandı: ${d.port}`, 'success');
        $('btnStream').disabled = false;
        $('btnCalibrate').disabled = false;
        setMode('csi');
        $('sysPort').textContent = d.port;
    } else {
        toast(d.error || 'Bağlanamadı', 'error');
    }
}

async function toggleStream() {
    if (!streaming) {
        const d = await api('/api/csi/stream/start', 'POST');
        if (d.success) {
            streaming = true;
            $('btnStream').innerHTML = '<i class="fas fa-stop"></i> Durdur';
            $('btnStream').classList.add('active');
            toast('CSI stream başladı', 'success');
        }
    } else {
        await api('/api/csi/stream/stop', 'POST');
        streaming = false;
        $('btnStream').innerHTML = '<i class="fas fa-play"></i> Başlat';
        $('btnStream').classList.remove('active');
    }
}

async function doCalibrate() {
    toast('Odayı boş bırakın (3sn)...', 'info');
    await api('/api/calibration/start', 'POST');
    setTimeout(async () => {
        const d = await api('/api/calibration/finish', 'POST');
        if (d.success) { toast('Kalibre edildi ✓', 'success'); $('sysCal').textContent = '✓'; }
        else toast('Başarısız - tekrar deneyin', 'error');
    }, 3500);
}

// ═══════ SOCKET ═══════
function initSocket() {
    socket.on('csi_update', handleUpdate);
    socket.on('connect', () => {});
    socket.on('disconnect', () => setMode('disconnected'));
}

function handleUpdate(data) {
    if (!data) return;
    updatePresence(data.presence);
    updateSignal(data.signal);
    updateSystem(data.system);
    updateGraphs(data.graphs);
}

// ═══════ PRESENCE ═══════
function updatePresence(p) {
    if (!p) return;
    
    const stateEl = $('heroState');
    const detailEl = $('heroDetail');
    const iconEl = $('heroIcon');
    
    // State class
    const classMap = {
        'EMPTY':'s-empty','NO_SIGNAL':'s-no-signal','CALIBRATING':'s-calibrating',
        'POSSIBLE_PRESENCE':'s-possible','PRESENT_STATIC':'s-static',
        'MOVING':'s-moving','APPROACHING':'s-approach','MOVING_AWAY':'s-away',
        'SIGNAL_UNRELIABLE':'s-unreliable'
    };
    stateEl.className = 'hero-state ' + (classMap[p.state] || '');
    
    // Label
    const labels = {
        'NO_SIGNAL':'SİNYAL YOK','CALIBRATING':'KALİBRASYON','EMPTY':'ORTAM BOŞ',
        'POSSIBLE_PRESENCE':'OLASI VARLIK','PRESENT_STATIC':'İNSAN TESPİT',
        'MOVING':'HAREKET','APPROACHING':'YAKLAŞIYOR','MOVING_AWAY':'UZAKLAŞIYOR',
        'SIGNAL_UNRELIABLE':'SİNYAL DÜŞÜK'
    };
    stateEl.textContent = labels[p.state] || p.state;
    
    // Detail
    const details = {
        'NO_SIGNAL':'ESP32 bağlayın veya stream başlatın',
        'CALIBRATING':'Boş oda referansı alınıyor...',
        'EMPTY':'Ortamda hareket veya varlık algılanmıyor',
        'POSSIBLE_PRESENCE':'Sinyal değişimi tespit, doğrulanıyor...',
        'PRESENT_STATIC':'Hareketsiz insan/nesne varlığı kesinleşti',
        'MOVING':'Aktif hareket tespit ediliyor',
        'APPROACHING':'Hedef sensöre doğru yaklaşıyor',
        'MOVING_AWAY':'Hedef sensörden uzaklaşıyor',
        'SIGNAL_UNRELIABLE':'Sinyal kalitesi yetersiz, ESP32 konumunu kontrol edin'
    };
    detailEl.textContent = details[p.state] || '';
    
    // Icon
    const icons = {
        'NO_SIGNAL':'fa-plug-circle-xmark','EMPTY':'fa-wind','CALIBRATING':'fa-spinner fa-spin',
        'POSSIBLE_PRESENCE':'fa-person-circle-question','PRESENT_STATIC':'fa-person',
        'MOVING':'fa-person-walking','APPROACHING':'fa-person-walking-arrow-right',
        'MOVING_AWAY':'fa-person-walking-arrow-loop-left','SIGNAL_UNRELIABLE':'fa-triangle-exclamation'
    };
    iconEl.innerHTML = `<i class="fas ${icons[p.state] || 'fa-ghost'}"></i>`;
    iconEl.classList.toggle('active', p.is_human_present);
    
    // Rings
    setRing('ringMotion', p.motion_level * 100);
    setRing('ringConfidence', p.confidence * 100);
    $('valMotion').textContent = Math.round(p.motion_level * 100) + '%';
    $('valConfidence').textContent = Math.round(p.confidence * 100) + '%';
    
    // Direction
    const dirMap = {'approaching':'↗ Yaklaşıyor','receding':'↙ Uzaklaşıyor','lateral':'↔ Yanal','static':'• Sabit','unknown':'-'};
    $('valDirection').textContent = dirMap[p.direction] || p.direction;
    
    // Direction icon rotation
    const dirAngle = {'approaching':-45,'receding':135,'lateral':0,'static':90};
    $('dirIcon').style.transform = `rotate(${dirAngle[p.direction]||0}deg)`;
    
    // State duration
    $('sysDuration').textContent = p.state_duration_s + 's';
    
    // Timeline
    if (p.last_events) updateTimeline(p.last_events);
}

// ═══════ SİNYAL ═══════
function updateSignal(s) {
    if (!s) return;
    $('liveRssi').textContent = s.rssi;
    $('liveSub').textContent = s.num_subcarriers;
    $('liveQuality').textContent = Math.round(s.quality * 100) + '%';
    $('sysSnr').textContent = s.snr.toFixed(1) + 'dB';
    
    setRing('ringQuality', s.quality * 100);
    $('valQuality').textContent = Math.round(s.quality * 100) + '%';
    
    $('gMotionVal').textContent = s.motion_score.toFixed(3);
    $('gEnergyVal').textContent = s.energy.toFixed(4);
}

// ═══════ SİSTEM ═══════
function updateSystem(sys) {
    if (!sys) return;
    $('livePps').textContent = sys.pps;
    $('sysFrames').textContent = sys.frames_total;
    if (sys.port) $('sysPort').textContent = sys.port;
    if (sys.calibrated) $('sysCal').textContent = '✓';
    $('sysUptime').textContent = sys.uptime || '0';
    
    const stats = sys.pipeline_stats || {};
    $('sysDrop').textContent = (stats.drop_rate * 100).toFixed(1) + '%';
}

// ═══════ GRAFİKLER ═══════
function updateGraphs(g) {
    if (!g) return;
    drawLine('graphMotion', g.motion, '#00f593', 0, 1);
    drawLine('graphEnergy', g.energy, '#00b4ff', 0, 0.3);
    if (g.amplitude && g.amplitude.length) {
        lastAmplitude = g.amplitude;
        drawBar('graphAmplitude', lastAmplitude);
        $('gAmpVal').textContent = lastAmplitude.length + ' sub';
    }
}

function drawLine(id, data, color, min, max) {
    const c = $(id); if (!c || !data || !data.length) return;
    const ctx = c.getContext('2d');
    const w = c.width = c.parentElement.clientWidth - 24;
    const h = c.height = 70;
    ctx.clearRect(0, 0, w, h);
    
    // Fill area
    ctx.beginPath();
    const range = (max - min) || 1;
    data.forEach((v, i) => {
        const x = (i/(data.length-1)) * w;
        const y = h - ((v-min)/range) * (h-6) - 3;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, color + '30');
    grad.addColorStop(1, color + '00');
    ctx.fillStyle = grad; ctx.fill();
    
    // Line
    ctx.beginPath();
    data.forEach((v, i) => {
        const x = (i/(data.length-1)) * w;
        const y = h - ((v-min)/range) * (h-6) - 3;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke();
}

function drawBar(id, data) {
    const c = $(id); if (!c || !data.length) return;
    const ctx = c.getContext('2d');
    const w = c.width = c.parentElement.clientWidth - 24;
    const h = c.height = 60;
    ctx.clearRect(0, 0, w, h);
    
    const barW = w / data.length;
    const max = Math.max(...data) || 1;
    
    data.forEach((v, i) => {
        const bh = (v / max) * (h - 4);
        const x = i * barW;
        const hue = 180 + (v/max) * 60; // cyan → green
        ctx.fillStyle = `hsla(${hue}, 80%, 55%, 0.7)`;
        ctx.fillRect(x, h - bh, barW - 0.5, bh);
    });
}

// ═══════ TIMELINE ═══════
function updateTimeline(events) {
    const tl = $('timeline');
    if (!events || !events.length) return;
    
    tl.innerHTML = '';
    events.slice().reverse().forEach(ev => {
        const el = document.createElement('div');
        el.className = 'tl-item';
        
        const dotClass = ev.to.includes('MOVING') || ev.to.includes('APPROACH') ? 'moving' :
                         ev.to.includes('PRESENT') || ev.to.includes('POSSIBLE') ? 'present' :
                         ev.to.includes('EMPTY') ? 'empty' : 'warn';
        
        const labels = {'EMPTY':'Boş','MOVING':'Hareket','PRESENT_STATIC':'Varlık',
            'APPROACHING':'Yaklaşma','MOVING_AWAY':'Uzaklaşma','POSSIBLE_PRESENCE':'Olası',
            'NO_SIGNAL':'Sinyal Yok','CALIBRATING':'Kalibrasyon','SIGNAL_UNRELIABLE':'Düşük Sinyal'};
        
        el.innerHTML = `
            <div class="tl-dot ${dotClass}"></div>
            <div class="tl-text"><b>${labels[ev.to]||ev.to}</b></div>
            <div class="tl-time">${Math.round(ev.confidence*100)}%</div>
        `;
        tl.appendChild(el);
    });
}

// ═══════ YARDIMCI ═══════
function setRing(id, percent) {
    const el = $(id);
    if (el) el.setAttribute('stroke-dasharray', `${Math.min(100, Math.max(0, percent))}, 100`);
}

function setMode(mode) {
    const chip = $('modeChip');
    chip.className = 'chip mode-chip ' + mode;
    const labels = {'disconnected':'DISCONNECTED','csi':'CSI LIVE','sim':'SIMULATION'};
    chip.innerHTML = `<i class="fas fa-circle"></i> ${labels[mode]||mode.toUpperCase()}`;
}

function toast(msg, type='info') {
    const c = $('toasts');
    const t = document.createElement('div');
    t.className = `toast ${type}`;
    const icons = {success:'fa-check-circle',error:'fa-circle-xmark',info:'fa-circle-info'};
    t.innerHTML = `<i class="fas ${icons[type]||icons.info}"></i> ${msg}`;
    c.appendChild(t);
    setTimeout(() => { t.style.opacity='0'; t.style.transform='translateX(30px)';
        t.style.transition='all 0.3s'; setTimeout(()=>t.remove(), 300); }, 4000);
}

async function api(url, method='GET', body=null) {
    const opts = {method, headers: {'Content-Type':'application/json'}};
    if (body !== null) opts.body = JSON.stringify(body);
    const res = await fetch(url, opts);
    return res.json();
}

function $(id) { return document.getElementById(id); }
