import soundfile as sf
import numpy as np
import librosa
import os

def process():
    # 1. Đường dẫn tới file nhạc mới bạn muốn test
    input_file = "/home/thinh_202414666/Downloads/Assignment1-main/sieutest.wav"
    
    # 2. Đường dẫn chính xác tới thư mục chứa file main.c của bạn
    sketch_path = "/home/thinh_202414666/Downloads/Assignment1-main/main"
    
    print(f"--- Đang đọc file: {input_file} ---\")")
    try:
        data, sr = sf.read(input_file)
    except Exception as e:
        print(f"❌ Lỗi không mở được file nhạc: {e}")
        print("Mẹo: Hãy kiểm tra lại xem file 'sieutest.wav' có nằm đúng thư mục trên không nhé!")
        return

    print(f"    Sample rate: {sr}Hz, Channels: {data.ndim}, Length: {len(data)/sr:.2f}s")
    
    # Chuyển sang Mono nếu là file Stereo
    if len(data.shape) > 1:
        data = data.mean(axis=1)
    
    # Resample sang 24000Hz (chuẩn cho hệ thống cấu hình I2S)
    if sr != 24000:
        data = librosa.resample(data, orig_sr=sr, target_sr=24000)
        print(f"    Đã resample sang 24000Hz")
    
    # ====================================================================
    # THUẬT TOÁN ĐẶC TRỊ: ÉP NHỎ BIÊN ĐỘ GỐC ĐỂ DẬP TIẾNG Ù RÈ LỚN
    # ====================================================================
    # Thay vì nhân với 32767 (làm vỡ biên độ nhiễu), ta chỉ nhân với 8000.
   # Cấu hình biên độ vàng 24000: Âm thanh tự nhiên, không bị méo kiểu radio
    audio_int16 = (np.clip(data, -1.0, 1.0) * 24000).astype(np.int16)
    
    print(f"    Số lượng samples: {len(audio_int16)}")
    
    best_bitrate = 6.0  
    print(f"\nChọn cấu hình: {best_bitrate} kbps\n")

    # --- GHI FILE .H (CHUẨN SẠCH CHO ESP-IDF) ---
    print(f"--- Ghi file audio_data.h cho ESP32 ---")
    header_full_path = os.path.join(sketch_path, "audio_data.h")
    
    try:
        with open(header_full_path, "w") as f:
            f.write(f"// Audio từ {input_file} (24000Hz, Mono, PCM 16-bit)\n")
            f.write(f"// Tổng samples: {len(audio_int16)}\n")
            f.write(f"// Thời gian: {len(audio_int16)/24000:.2f}s\n\n")
            f.write("#include <stdint.h>\n\n")
            f.write(f"const int16_t audio_data[] = {{\n")
            
            # Ghi mảng số int16 xếp chồng hàng loạt mượt mà, tối ưu dung lượng file text
            for idx, sample in enumerate(audio_int16):
                f.write(f"{sample},")
                if (idx + 1) % 15 == 0:
                    f.write("\n")
                    
            f.write("\n};\n\n")
            f.write(f"const int audio_len = {len(audio_int16)};\n")
            
        print(f"✅ ĐÃ XUẤT FILE THÀNH CÔNG: {header_full_path}")
        print("Mẹo: Giờ bạn có thể chạy 'idf.py build flash monitor' để kiểm tra chất âm.")
    except Exception as e:
        print(f"❌ Lỗi khi ghi file .h: {e}")

if __name__ == "__main__":
    process()
