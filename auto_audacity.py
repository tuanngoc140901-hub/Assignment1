import socket
import wave
import threading
import sys

# ====================================================================
# CẤU HÌNH IP VÀ CỔNG MẠNG (ĐỒNG BỘ VỚI ESP32)
# ====================================================================
HOST_IP = "0.0.0.0"      # Lắng nghe trên tất cả các card mạng của Ubuntu
PORT_RAW = 12345         # Cổng nhận âm thanh gốc (Original)
PORT_PROC = 12346        # Cổng nhận âm thanh đã qua lọc DSP (Processed)

# ĐỒNG BỘ CHUẨN TẦN SỐ VỚI BÀI LAB VÀ ESP32
SAMPLE_RATE = 24000      

# Biến cờ hiệu khống chế luồng ghi
is_recording = True

def receive_stream(port, filename):
    global is_recording
    print(f"[*] Đang lắng nghe trên Port {port} -> Tiến trình sẽ lưu vào: {filename}")   
    
    # Khởi tạo Socket UDP (SOCK_DGRAM)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        sock.bind((HOST_IP, port))  
    except Exception as e:
        print(f"[!] Lỗi không thể bind Port {port}: {e}")
        sock.close()
        return

    # Khởi tạo cấu trúc file WAV tiêu chuẩn 16-bit Mono
    wav_file = wave.open(filename, 'wb')
    wav_file.setnchannels(1)      # Kênh đơn Mono
    wav_file.setsampwidth(2)      # 16-bit PCM (2 bytes)
    wav_file.setframerate(SAMPLE_RATE)   
    
    try:
        while is_recording:
            # Nhận gói tin UDP (Cấu hình bộ đệm 4096 bytes để thoải mái nhận khối dữ liệu)
            data, addr = sock.recvfrom(4096)            
            
            # Kiểm tra gói tin Trigger kết thúc bài hát hoặc ngắt luồng
            if data == b"EOF":
                break               
            
            if data:
                # Ghi trực tiếp mảng bytes PCM nhị phân vào file WAV
                wav_file.writeframes(data)
                
    except Exception as e:
        print(f"[!] Lỗi xảy ra trong quá trình thu luồng Port {port}: {e}")
    finally:
        wav_file.close()
        sock.close()
        print(f"[-] Đã đóng và bảo toàn file: {filename}")

if __name__ == "__main__":
    print("=" * 60)
    print("      HỆ THỐNG GHI ÂM THỜI GIAN THỰC QUA WI-FI (UDP SOCKET)      ")
    print("=" * 60)
    
    # Khởi chạy 2 luồng song song để bắt trọn vẹn 2 cổng cùng một lúc
    t_raw = threading.Thread(target=receive_stream, args=(PORT_RAW, "original.wav"))
    t_proc = threading.Thread(target=receive_stream, args=(PORT_PROC, "processed.wav"))  
    
    t_raw.start()
    t_proc.start()   
    
    try:
        print("\n[+] HỆ THỐNG ĐANG GHI ÂM NGẦM...")
        print("[!] Mẹo: Hãy bấm nút RESET (EN) trên ESP32 để mạch kết nối Wi-Fi và phát nhạc.")
        input("\n==> Nhấn phím [ENTER] bất kỳ lúc nào để DỪNG thu và ĐÓNG FILE...\n")
    except KeyboardInterrupt:
        pass   
    
    # Hạ cờ hiệu ngắt vòng lặp nhận tin ở các Thread
    is_recording = False 
    
    # Gửi gói tin mồi (Dummy packet) "EOF" tự gửi cho chính mình để giải phóng hàm sock.recvfrom() đang bị block
    dummy_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        dummy_sock.sendto(b"EOF", ("127.0.0.1", PORT_RAW))
        dummy_sock.sendto(b"EOF", ("127.0.0.1", PORT_PROC))  
    except Exception:
        pass
    dummy_sock.close()
    
    # Đợi các luồng thu dọn tài nguyên và đóng file hoàn toàn
    t_raw.join()
    t_proc.join()
    print("\n[V] THÀNH CÔNG! Cả 2 file 'original.wav' và 'processed.wav' đã được lưu an toàn.")
