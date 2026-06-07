import soundfile as sf
import numpy as np
import librosa
import os

def process():
    # Sử dụng đường dẫn tuyệt đối để Python luôn tìm thấy file dù bạn đứng ở thư mục nào
    input_file = "/home/ngoc-202414649/data/Arduino/esp32_audio_lab/test5.wav"
    sketch_path = "/home/ngoc-202414649/data/Assign1/main"  
    
    print(f"--- Đang đọc file: {input_file} ---")
    try:
        data, sr = sf.read(input_file)
    except Exception as e:
        print(f"❌ Lỗi không mở được file nhạc: {e}")
        print("Mẹo: Hãy kiểm tra lại xem file 'test5.wav' có nằm đúng thư mục trên không nhé!")
        return

    print(f"    Sample rate: {sr}Hz, Channels: {data.ndim}, Length: {len(data)/sr:.2f}s")
    
    # Chuyển sang Mono
    if len(data.shape) > 1:
        data = data.mean(axis=1)
    
    # Resample sang 24000Hz (chuẩn cho hệ thống)
    if sr != 24000:
        data = librosa.resample(data, orig_sr=sr, target_sr=24000)
        print(f"    Đã resample sang 24000Hz")
    
    # Normalize và chuyển sang 16-bit PCM
    data = np.clip(data, -1.0, 1.0) * 32767
    audio_int16 = data.astype(np.int16)
    
    print(f"    Số lượng samples: {len(audio_int16)}")
    
    best_bitrate = 6.0  
    print(f"\nChọn cấu hình: {best_bitrate} kbps\n")

    # --- GHI FILE .H (CHUẨN SẠCH CHO ESP-IDF) ---
    print(f"--- Ghi file audio_data.h cho ESP32 ---")
    header_full_path = os.path.join(sketch_path, "audio_data.h")
    
    with open(header_full_path, "w") as f:
        f.write(f"// Audio từ {input_file} (24000Hz, Mono, PCM 16-bit)\n")
        f.write(f"// Tổng samples: {len(audio_int16)}\n")
        f.write(f"// Thời gian: {len(audio_int16)/24000:.2f}s\n\n")
        
        # Thêm thư viện tiêu chuẩn để C nhận diện int16_t
        f.write("#include <stdint.h>\n\n") 
        
        # Khai báo mảng const chuẩn C, bỏ hẳn chữ PROGMEM lỗi thời của Arduino
        f.write("const int16_t audio_data[] = {\n")
        for i, val in enumerate(audio_int16):
            f.write(f"{val},")
            if (i + 1) % 15 == 0: f.write("\n")
        f.write("\n};\n\n")
        f.write(f"const int audio_len = {len(audio_int16)};\n")
    
    print(f"✅ Đã tạo thành công: {header_full_path}")

if __name__ == "__main__":
    process()