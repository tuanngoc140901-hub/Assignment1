import socket
import wave
import threading
import json
import psutil
import time
import os
from flask import Flask, render_template_string
from flask_socketio import SocketIO

HOST_IP = "0.0.0.0"      
PORT_PROC = 12346        
SAMPLE_RATE = 24000      

# Định nghĩa hằng số tổng RAM ước tính khả dụng cho ứng dụng trên ESP32
ESP32_TOTAL_RAM_KIB = 240.0

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

is_recording = False  
stats_lock = threading.Lock()

iot_dashboard_stats = {
    "port_proc": PORT_PROC,
    "proc_packet_count": 0,
    "proc_lost_packets": 0,
    "proc_loss_rate": "0.00 %",
    "esp_cpu": 0,
    "esp_free_ram_kb": 0,
    "esp_uptime_sec": 0,
    "status": "Đang chờ kích hoạt hệ thống..."
}

def receive_iot_stream(port, filename):
    global is_recording, iot_dashboard_stats
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST_IP, port)) 
    except Exception as e:
        print(f"[!] Lỗi khởi tạo cổng UDP: {e}")
        return

    wav_file = wave.open(filename, 'wb')
    wav_file.setnchannels(1)     
    wav_file.setsampwidth(2)     
    wav_file.setframerate(SAMPLE_RATE)  
    
    expected_seq = 0
    is_first_packet = True

    try:
        while is_recording:
            sock.settimeout(1.0)
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
                         
            if not data or data == b"EOF":
                print("[*] Đã nhận tín hiệu kết thúc luồng (EOF) từ khối I2S ESP32.")
                break               
            
            if len(data) > 1028:
                packet_seq = int.from_bytes(data[:4], byteorder='little')
                
                lost_count = 0
                if not is_first_packet and packet_seq != expected_seq:
                    diff = packet_seq - expected_seq
                    if diff > 0:
                        lost_count = diff
                is_first_packet = False
                expected_seq = packet_seq + 1
                
                audio_payload = data[4:1028]
                wav_file.writeframes(audio_payload)
                
                try:
                    json_str = data[1028:].decode('utf-8').strip()
                    iot_meta = json.loads(json_str)
                except Exception:
                    iot_meta = {"cpu": 0, "ram": 0, "uptime": 0}
                
                with stats_lock:
                    iot_dashboard_stats["proc_packet_count"] += 1
                    iot_dashboard_stats["proc_lost_packets"] += lost_count
                    total_exp = iot_dashboard_stats["proc_packet_count"] + iot_dashboard_stats["proc_lost_packets"]
                    iot_dashboard_stats["proc_loss_rate"] = f"{(iot_dashboard_stats['proc_lost_packets'] / total_exp * 100):.2f} %"
                    
                    iot_dashboard_stats["esp_cpu"] = iot_meta.get("cpu", 0)
                    iot_dashboard_stats["esp_free_ram_kb"] = iot_meta.get("ram", 0)
                    iot_dashboard_stats["esp_uptime_sec"] = iot_meta.get("uptime", 0)

    except Exception as e:
        print(f"[!] Lỗi xử lý luồng mạng UDP: {e}")
    finally:
        wav_file.close()
        sock.close()
        
        is_recording = False
        
        print("[-] Đóng luồng thu âm thành công.")
        with stats_lock:
            iot_dashboard_stats["status"] = "Đã kết xuất thành công tập tin: result.wav"
            socketio.emit('status_update', {'status': iot_dashboard_stats["status"]})

def system_monitor_thread():
    while is_recording:
        laptop_cpu = psutil.cpu_percent(interval=None)
        virtual_mem = psutil.virtual_memory()
        laptop_mem_used_gib = virtual_mem.used / (1024 ** 3)
        laptop_mem_total_gib = virtual_mem.total / (1024 ** 3)
        laptop_mem_percent = virtual_mem.percent
        
        with stats_lock:
            stats_copy = iot_dashboard_stats.copy()
        
        # Kiểm tra giới hạn dữ liệu RAM trống của ESP32
        free_ram_kib = stats_copy["esp_free_ram_kb"]
        if free_ram_kib > ESP32_TOTAL_RAM_KIB: 
            free_ram_kib = ESP32_TOTAL_RAM_KIB
            
        # Xử lý logic chặn nhảy vọt 100% khi chưa có kết nối UDP từ board ESP32
        if stats_copy["proc_packet_count"] == 0:
            esp_ram_used_pct = 0.0
            esp_ram_text = "Đang chờ kết nối..."
            esp_cpu_display = 0.0
        else:
            esp_ram_used_pct = ((ESP32_TOTAL_RAM_KIB - free_ram_kib) / ESP32_TOTAL_RAM_KIB) * 100.0
            esp_ram_text = f"{free_ram_kib} / {int(ESP32_TOTAL_RAM_KIB)} KiB tự do"
            esp_cpu_display = stats_copy["esp_cpu"]
        
        socketio.emit('iot_stats_update', {
            'laptop_cpu': laptop_cpu,
            'laptop_mem_pct': laptop_mem_percent,
            'laptop_mem_text': f"{laptop_mem_used_gib:.1f} / {laptop_mem_total_gib:.1f} GiB",
            'esp_cpu': esp_cpu_display,
            'esp_ram_pct': esp_ram_used_pct, 
            'esp_free_ram_text': esp_ram_text,
            'esp_uptime': stats_copy["esp_uptime_sec"],
            'packets': stats_copy["proc_packet_count"],
            'loss_rate': stats_copy["proc_loss_rate"],
            'status': stats_copy["status"]
        })
        time.sleep(0.5)

@app.route('/')
def index():
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="UTF-8">
        <title>Dashboard Thu Thập Telemetry IoT Audio</title>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body { font-family: 'Segoe UI', sans-serif; background-color: #0f172a; color: #e2e8f0; padding: 20px; }
            .grid-container { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 20px; }
            .card { background-color: #1e293b; border-radius: 10px; padding: 20px; border: 1px solid #334155; }
            h2, h3 { color: #38bdf8; margin-top: 0; }
            .metric { display: flex; justify-content: space-between; margin-bottom: 12px; font-size: 15px; }
            .value { font-weight: bold; color: #f8fafc; }
            
            .chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
            .chart-box { background-color: #1e293b; border-radius: 10px; padding: 15px; border: 1px solid #334155; height: 260px; }
            
            .btn-container { text-align: center; margin-bottom: 25px; }
            .play-btn {
                background-color: #10b981; color: white; border: none; padding: 15px 40px;
                font-size: 18px; font-weight: bold; border-radius: 30px; cursor: pointer;
                box-shadow: 0 4px 14px rgba(16, 185, 129, 0.4); transition: all 0.2s ease;
            }
            .play-btn:hover { background-color: #059669; transform: scale(1.05); }
            .play-btn:disabled { background-color: #475569; cursor: not-allowed; box-shadow: none; }
            #status_banner { font-size: 16px; font-weight: bold; text-align: center; color: #fbbf24; margin-bottom: 20px; }
        </style>
    </head>
    <body>
        <h2>TRUNG TÂM GIÁM SÁT REAL-TIME ĐỒNG BỘ & ĐO LƯỜNG SÓNG IOT EDGE</h2>
        
        <div class="btn-container">
            <button class="play-btn" id="start_btn" onclick="startSystem()">▶ KÍCH HOẠT HỆ THỐNG (PLAY)</button>
        </div>
        <div id="status_banner">Trạng thái: Đang chờ lệnh khởi động...</div>

        <div class="grid-container">
            <div class="card">
                <h3>1. Số Liệu Đường Truyền UDP</h3>
                <div class="metric"><span>Cổng Mạng Lắng Nghe:</span><span class="value">12346 / UDP</span></div>
                <div class="metric"><span>Tổng Số Gói Đã Nhận:</span><span class="value" id="pkts_lbl">0 Khối</span></div>
                <div class="metric"><span>Tỷ Lệ Tổn Thất Gói (Loss):</span><span class="value" id="loss_lbl" style="color:#ef4444;">0.00 %</span></div>
                <div class="metric"><span>Thời Gian Hoạt Động ESP32:</span><span class="value" id="uptime_lbl">0 s</span></div>
            </div>
            <div class="card">
                <h3>2. Trạng Thái Tài Nguyên Tức Thời</h3>
                <div class="metric"><span>Tải CPU Máy Tính:</span><span class="value" id="l_cpu">0.0 %</span></div>
                <div class="metric"><span>Bộ Nhớ RAM Máy Tính:</span><span class="value" id="l_mem">0.0 GiB</span></div>
                <div class="metric"><span>Tải CPU Node ESP32:</span><span class="value" id="e_cpu" style="color:#f43f5e;">0.0 %</span></div>
                <div class="metric"><span>Bộ Nhớ RAM ESP32 Đang Dùng:</span><span class="value" id="e_ram" style="color:#34d399;">0.0 %</span></div>
            </div>
        </div>

        <div class="chart-grid">
            <div class="chart-box">
                <h3>3. CPU (%)</h3>
                <canvas id="laptopCpuChart"></canvas>
            </div>
            <div class="chart-box">
                <h3>4. CPU ESP32(%)</h3>
                <canvas id="espCpuChart"></canvas>
            </div>
            <div class="chart-box">
                <h3>5. RAM MÁY TÍNH (%)</h3>
                <canvas id="laptopRamChart"></canvas>
            </div>
            <div class="chart-box">
                <h3>6. RAM ESP-32 (%)</h3>
                <canvas id="espRamChart"></canvas>
            </div>
        </div>

        <script>
            var socket = io();
            var tickCounter = 0;
            
            function startSystem() {
                document.getElementById('start_btn').disabled = true;
                document.getElementById('start_btn').innerText = "🔊 ĐANG THU DỮ LIỆU...";
                socket.emit('trigger_play');
            }

            function createChartConfig(label, color, yTitle, yMin, yMax) {
                return {
                    type: 'line',
                    data: { labels: [], datasets: [{ label: label, borderColor: color, data: [], tension: 0.15, fill: false }] },
                    options: {
                        responsive: true, maintainAspectRatio: false,
                        scales: {
                            x: { grid: { color: '#334155' }, ticks: { color: '#94a3b8' } },
                            y: { min: yMin, max: yMax, title: { display: true, text: yTitle, color: '#e2e8f0' }, grid: { color: '#334155' }, ticks: { color: '#94a3b8' } }
                        },
                        plugins: { legend: { labels: { color: '#e2e8f0' } } }
                    }
                };
            }

            // Đồng bộ toàn bộ 4 thang đo về phạm vi cố định 0 - 100% để giao diện đồng nhất, cân xứng
            var lCpuChart = new Chart(document.getElementById('laptopCpuChart').getContext('2d'), createChartConfig('CPU Laptop (%)', '#38bdf8', 'Mức sử dụng (%)', 0, 100));
            var eCpuChart = new Chart(document.getElementById('espCpuChart').getContext('2d'), createChartConfig('CPU ESP32 (%)', '#f43f5e', 'Mức sử dụng (%)', 0, 100));
            var lRamChart = new Chart(document.getElementById('laptopRamChart').getContext('2d'), createChartConfig('RAM Laptop (%)', '#fbbf24', 'Tỷ lệ sử dụng (%)', 0, 100));
            var eRamChart = new Chart(document.getElementById('espRamChart').getContext('2d'), createChartConfig('RAM ESP32 (%)', '#10b981', 'Tỷ lệ sử dụng (%)', 0, 100));

            socket.on('iot_stats_update', function(data) {
                document.getElementById('status_banner').innerText = "Trạng thái: " + data.status;
                document.getElementById('l_cpu').innerText = data.laptop_cpu.toFixed(1) + " %";
                document.getElementById('l_mem').innerText = data.laptop_mem_text; // Sửa lỗi gọi biến laptop_text cũ
                document.getElementById('e_cpu').innerText = data.esp_cpu.toFixed(1) + " %";
                
                // Hiển thị phần trăm sử dụng kèm chú thích dung lượng thô ở giao diện card số 2
                if (data.packets === 0) {
                    document.getElementById('e_ram').innerText = "0.0 % (" + data.esp_free_ram_text + ")";
                } else {
                    document.getElementById('e_ram').innerText = data.esp_ram_pct.toFixed(1) + " % (" + data.esp_free_ram_text + ")";
                }
                
                document.getElementById('pkts_lbl').innerText = data.packets + " Khối";
                document.getElementById('loss_lbl').innerText = data.loss_rate;
                document.getElementById('uptime_lbl').innerText = data.esp_uptime + " s";

                tickCounter += 0.5;
                var timeLabel = tickCounter.toFixed(1) + " s";
                
                [lCpuChart, eCpuChart, lRamChart, eRamChart].forEach(function(chart) {
                    chart.data.labels.push(timeLabel);
                    if(chart.data.labels.length > 30) { chart.data.labels.shift(); chart.data.datasets[0].data.shift(); }
                });

                lCpuChart.data.datasets[0].data.push(data.laptop_cpu);
                eCpuChart.data.datasets[0].data.push(data.esp_cpu);
                lRamChart.data.datasets[0].data.push(data.laptop_mem_pct);
                eRamChart.data.datasets[0].data.push(data.esp_ram_pct);

                lCpuChart.update(); eCpuChart.update(); lRamChart.update(); eRamChart.update();
            });

            socket.on('status_update', function(data) {
                document.getElementById('status_banner').innerText = "Trạng thái: " + data.status;
                if(data.status.includes("thành công")) {
                    document.getElementById('start_btn').innerText = "▶ KHỞI CHẠY LẠI (PLAY)";
                    document.getElementById('start_btn').disabled = false;
                }
            });
        </script>
    </body>
    </html>
    """)

@socketio.on('trigger_play')
def handle_trigger_play():
    global is_recording, iot_dashboard_stats
    if not is_recording:
        is_recording = True
        
        with stats_lock:
            iot_dashboard_stats["proc_packet_count"] = 0
            iot_dashboard_stats["proc_lost_packets"] = 0
            iot_dashboard_stats["proc_loss_rate"] = "0.00 %"
            iot_dashboard_stats["status"] = "Đường truyền đang mở... Hãy bật / reset board ESP32!"
            
        t_proc = threading.Thread(target=receive_iot_stream, args=(PORT_PROC, "result.wav"))  
        t_monitor = threading.Thread(target=system_monitor_thread)
        t_proc.start()   
        t_monitor.start()

if __name__ == "__main__":
    print("=" * 60)
    print("      KHỞI CHẠY GATEWAY ĐIỀU KHIỂN ĐỒNG BỘ CẢM BIẾN AUDIO     ")
    print("=" * 60)
    print("[+] Đang mở Dashboard đo lường tại địa chỉ: http://localhost:5000")
    
    try:
        socketio.run(app, host="0.0.0.0", port=5000, log_output=False)
    except KeyboardInterrupt:
        pass
        
    is_recording = False
    print("\n[V] Đã đóng máy chủ xử lý an toàn.")
