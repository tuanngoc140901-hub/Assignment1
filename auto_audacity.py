import os
import re
import time
import numpy as np
import soundfile as sf
import subprocess

# Chờ 1 giây đảm bảo Ubuntu ghi xong file log xuống bộ nhớ đệm đĩa cứng
time.sleep(1.0) 

log_file_path = "build/esp32_output.txt"
print("🔄 [Host PC] Đang tiến hành bóc tách dữ liệu từ file log...")

if not os.path.exists(log_file_path):
    print(f"❌ Không tìm thấy file log tại: {log_file_path}")
    exit()

with open(log_file_path, "r", encoding="utf-8", errors="ignore") as f:
    content = f.read()

# Tìm khối dữ liệu được kẹp giữa hai tag
data_match = re.search(r"---START_DATA_CSV---\n(.*?)\n---END_DATA_CSV---", content, re.DOTALL)

if not data_match:
    print("❌ Lỗi: Không thể tìm thấy khối dữ liệu CSV âm thanh trong file log!")
    exit()

csv_lines = data_match.group(1).strip().split("\n")
orig_signals = []
filtered_signals = []

for line in csv_lines[1:]:
    parts = line.strip().split(",")
    if len(parts) >= 4:
        try:
            orig_signals.append(int(parts[1]))
            filtered_signals.append(int(parts[2]))
        except ValueError:
            continue

if len(orig_signals) == 0:
    print("❌ Dữ liệu mảng trích xuất bị rỗng!")
    exit()

# Chuẩn hóa biên độ tín hiệu về dải từ -1.0 đến 1.0 cho file WAV
orig_array = np.array(orig_signals, dtype=np.float32) / 32767.0
filtered_array = np.array(filtered_signals, dtype=np.float32) / 32767.0

sf.write("original_signal.wav", orig_array, 24000)
sf.write("filtered_signal.wav", filtered_array, 24000)

print("✅ Trích xuất thành công: original_signal.wav và filtered_signal.wav")
print("🚀 Đang khởi động Audacity để vẽ biểu đồ so sánh âm tần...")

try:
    # Kích hoạt Audacity chạy độc lập dưới nền để không treo terminal
    subprocess.Popen(["audacity", "original_signal.wav", "filtered_signal.wav"], 
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("🎉 Audacity đã được mở lên màn hình!")
except FileNotFoundError:
    print("❌ Lỗi: Hệ thống chưa cài Audacity. Hãy gõ lệnh: sudo apt install audacity")