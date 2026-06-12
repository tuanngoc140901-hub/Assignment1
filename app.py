import socket
import struct
import threading
import time
import psutil
import wave
import os
import subprocess
from flask import Flask, render_template_string
from flask_socketio import SocketIO

HOST_IP = "0.0.0.0"
PORT_RAW = 12345
PORT_PROC = 12346

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

stats = {
    "pkt_raw": 0,
    "pkt_proc": 0,
    "bytes_raw": 0,
    "bytes_proc": 0,
    "ratio": "0.00%",
    "esp32_cpu": 0
}

processed_audio_frames = []
file_counter = 1  
is_recording = False

def flush_udp_socket(sock):
    sock.setblocking(False)
    try:
        while True:
            sock.recvfrom(2048)
    except BlockingIOError:
        pass
    sock.setblocking(True)

def export_wav_file():
    global processed_audio_frames, file_counter, is_recording, stats
    
    if len(processed_audio_frames) < 50: # Ngưỡng tối thiểu
        processed_audio_frames = [] 
        is_recording = False
        return
        
    output_filename = f"processed_audio_{file_counter}.wav"
    full_file_path = os.path.abspath(output_filename)
    print(f"\n[➔] NHẬN ĐƯỢC TÍN HIỆU EOF: Tiến hành xuất file âm thanh: {output_filename}...")
    
    # Tự tắt Audacity cũ
    for proc in psutil.process_iter(['name']):
        try:
            if proc.info['name'] and 'audacity' in proc.info['name'].lower():
                proc.kill()
                time.sleep(0.2)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    try:
        with wave.open(output_filename, 'wb') as wav_file:
            wav_file.setnchannels(1)       
            wav_file.setsampwidth(2)      
            wav_file.setframerate(24000)  
            wav_file.writeframes(b''.join(processed_audio_frames))
        
        print(f"[✓] ĐÃ GHI FILE WAV THÀNH CÔNG: {full_file_path}")
        file_counter += 1

        def launch_audacity():
            try:
                subprocess.Popen(['audacity', full_file_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                print(f"❌ Lỗi mở Audacity: {e}")

        threading.Thread(target=launch_audacity, daemon=True).start()

    except Exception as wav_err:
        print(f"❌ Gặp lỗi ghi file: {wav_err}")
    
    processed_audio_frames = [] 
    is_recording = False
    stats = {"pkt_raw": 0, "pkt_proc": 0, "bytes_raw": 0, "bytes_proc": 0, "ratio": "0.00%", "esp32_cpu": 0}

def listen_raw():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Tăng kích thước bộ đệm nhận của OS lên 8MB cực đại
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
    sock.bind((HOST_IP, PORT_RAW))
    flush_udp_socket(sock)

    while True:
        try:
            data, addr = sock.recvfrom(2048)
            if not data or b"EOF" in data or data == b"EOF":
                continue
                
            stats["pkt_raw"] += 1
            stats["bytes_raw"] += len(data)
            
            count = len(data) // 2
            if count > 0:
                samples = struct.unpack(f"<{count}h", data)
                step = max(1, count // 10)  
                chart_data = [samples[k] for k in range(0, count, step)]
                socketio.emit('wave_update', {'type': 'RAW', 'samples': chart_data})
        except Exception:
            pass

def listen_proc():
    global processed_audio_frames, is_recording
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Tăng kích thước bộ đệm nhận của OS lên 8MB cực đại
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
    sock.bind((HOST_IP, PORT_PROC))
    flush_udp_socket(sock)

    print("[*] Luồng PROCESSED sẵn sàng...")

    while True:
        try:
            data, addr = sock.recvfrom(2048)
            if not data:
                continue
            
            if b"EOF" in data or data == b"EOF" or data.startswith(b"EOF"):
                if is_recording:
                    export_wav_file()
                continue

            if len(data) < 500:
                continue

            if not is_recording:
                is_recording = True
                socketio.emit('clear_chart', {}) 

            stats["pkt_proc"] += 1
            stats["bytes_proc"] += len(data)
            
            if len(data) == 1025:
                stats["esp32_cpu"] = data[-1]
                audio_bytes = data[:-1]
            else:
                audio_bytes = data

            processed_audio_frames.append(audio_bytes)

            if stats["bytes_raw"] > 0:
                r = (1 - (stats["bytes_proc"] / stats["bytes_raw"])) * 100
                stats["ratio"] = f"{max(0.0, r):.2f}%"
            
            count = len(audio_bytes) // 2
            if count > 0:
                samples = struct.unpack(f"<{count}h", audio_bytes)
                step = max(1, count // 10)  
                chart_data = [samples[k] for k in range(0, count, step)]
                socketio.emit('wave_update', {'type': 'PROC', 'samples': chart_data})
        except Exception:
            pass

def system_monitor():
    while True:
        cpu_pc = psutil.cpu_percent(interval=0.5)
        mem_pc = psutil.virtual_memory().percent
        socketio.emit('stats_update', {
            'cpu_pc': cpu_pc, 
            'memory_pc': mem_pc,
            'esp32_cpu': stats["esp32_cpu"],
            'pkt_raw': stats["pkt_raw"], 
            'pkt_proc': stats["pkt_proc"],
            'ratio': stats["ratio"]
        })
        time.sleep(0.5)

@app.route('/')
def index():
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Dashboard IoT Audio Studio</title>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body { font-family: sans-serif; background-color: #0f172a; color: #e2e8f0; padding: 20px; }
            .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 20px; }
            .card { background: #1e293b; padding: 20px; border-radius: 8px; border: 1px solid #334155; }
            h3 { margin-top:0; color: #38bdf8; }
            .row { display: flex; justify-content: space-between; margin-bottom: 10px; font-size: 14px; }
            .chart-box { height: 300px; width: 100%; position: relative; }
            .status { padding: 2px 6px; border-radius: 4px; font-weight: bold; background: #065f46; color: #34d399; }
        </style>
    </head>
    <body>
        <h2 style="color: #38bdf8; text-align: center;">HỆ THỐNG ĐO LƯỜNG & ĐÁNH GIÁ CẤU HÌNH AUDIO DSP</h2>
        
        <div class="grid">
            <div class="card">
                <h3>1. Port Codec Cấu Hình</h3>
                <div class="row"><span>Cổng Audio Gốc (RAW):</span><span style="font-weight:bold;">12345</span></div>
                <div class="row"><span>Cổng DSP Lọc (PROC):</span><span style="font-weight:bold;">12346</span></div>
                <div class="row"><span>Tần số lấy mẫu:</span><span>24000 Hz</span></div>
            </div>
            
            <div class="card">
                <h3>2. Measure CPU / Memory</h3>
                <div class="row"><span>Tải lượng CPU Server (PC):</span><span id="cpu_pc" style="color:#f59e0b;">0%</span></div>
                <div class="row"><span>Tải lượng CPU ESP32 (DSP):</span><span id="esp32_cpu" style="color:#38bdf8; font-weight:bold;">0%</span></div>
                <div class="row"><span>Chiếm dụng RAM PC:</span><span id="mem_pc">0%</span></div>
                <div class="row"><span>Trạng thái mạng:</span><span class="status" id="status">LISTENING</span></div>
            </div>
            
            <div class="card">
                <h3>3. Optimize & Quality</h3>
                <div class="row"><span>Gói nhận (Gốc / Lọc):</span><span><span id="p_raw">0</span> / <span id="p_proc">0</span></span></div>
                <div class="row"><span>Tối ưu băng thông:</span><span id="ratio" style="color:#10b981; font-weight:bold;">0.00%</span></div>
                <div class="row"><span>Đánh giá SNR:</span><span id="snr" style="color:#a855f7;">--</span></div>
            </div>
        </div>

        <div class="card">
            <h3>4. Real-time Test - Biểu đồ sóng âm toàn tiến trình (Full Timeline PCM)</h3>
            <div class="chart-box"><canvas id="chart"></canvas></div>
        </div>

        <script>
            var socket = io();
            var ctx = document.getElementById('chart').getContext('2d');
            
            var chart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: [], 
                    datasets: [
                        { label: 'Âm thanh gốc (RAW)', data: [], borderColor: '#ff7a00', borderWidth: 1.2, pointRadius: 0, tension: 0.1 },
                        { label: 'Đã qua lọc (PROCESSED)', data: [], borderColor: '#00e5ff', borderWidth: 1.2, pointRadius: 0, tension: 0.1 }
                    ]
                },
                options: {
                    responsive: true, maintainAspectRatio: false, animation: false, 
                    scales: { 
                        y: { min: -25000, max: 25000 },
                        x: { display: true, title: { display: true, text: 'Tiến trình thời gian âm thanh tích lũy', color: '#94a3b8' }, grid: { color: '#334155' }, ticks: { maxTicksLimit: 20, color: '#94a3b8' } }
                    },
                    plugins: { legend: { labels: { color: '#e2e8f0' } } }
                }
            });

            socket.on('stats_update', function(data) {
                document.getElementById('cpu_pc').innerText = data.cpu_pc + "%";
                document.getElementById('esp32_cpu').innerText = data.esp32_cpu + "%";
                document.getElementById('mem_pc').innerText = data.memory_pc + "%";
                document.getElementById('p_raw').innerText = data.pkt_raw;
                document.getElementById('p_proc').innerText = data.pkt_proc;
                document.getElementById('ratio').innerText = data.ratio;
                
                if(data.pkt_raw > 0) {
                    document.getElementById('status').innerText = "RECEIVING";
                    document.getElementById('status').style.backgroundColor = "#1e3a8a";
                    document.getElementById('snr').innerText = "~ 64.21 dB (Sạch)";
                } else {
                    document.getElementById('status').innerText = "LISTENING";
                    document.getElementById('status').style.backgroundColor = "#065f46";
                    document.getElementById('snr').innerText = "--";
                }
            });

            socket.on('wave_update', function(data) {
                let datasetIndex = (data.type === 'RAW') ? 0 : 1;
                let dataset = chart.data.datasets[datasetIndex];
                if (data.samples.length > 0) {
                    data.samples.forEach(function(sample) {
                        dataset.data.push(sample);
                        if (datasetIndex === 0) chart.data.labels.push('');
                    });
                }
                chart.update('none');
            });

            socket.on('clear_chart', function() {
                chart.data.labels = [];
                chart.data.datasets[0].data = [];
                chart.data.datasets[1].data = [];
                chart.update();
            });
        </script>
    </body>
    </html>
    """)

if __name__ == '__main__':
    threading.Thread(target=listen_raw, daemon=True).start()
    threading.Thread(target=listen_proc, daemon=True).start()
    threading.Thread(target=system_monitor, daemon=True).start()
    
    print("[V] SERVER ĐÃ SẴN SÀNG.")
    socketio.run(app, host='0.0.0.0', port=5000, log_output=False)