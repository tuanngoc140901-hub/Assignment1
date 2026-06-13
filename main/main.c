#include <string.h>   
#include <stdio.h>    
#include <math.h>
#include "freertos/FreeRTOS.h" 
#include "freertos/task.h" 
#include "driver/i2s_std.h" 
#include "esp_err.h" 
#include "esp_log.h" 
#include "esp_wifi.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "lwip/err.h"
#include "lwip/sockets.h"
#include "audio_data.h"  
#include "esp_timer.h" 
#include "esp_system.h"

#define I2S_BCK_IO      GPIO_NUM_26 
#define I2S_WS_IO       GPIO_NUM_25 
#define I2S_DO_IO       GPIO_NUM_22 
#define SAMPLE_RATE     24000 
#define CHUNK_SIZE      512 

#define WIFI_SSID       "Phuong Lien/2G"     
#define WIFI_PASS       "12345689" 
#define SERVER_IP       "192.168.1.2"
#define PORT_PROC       12346   

// Các tham số cấu hình bộ lọc mềm cho âm thanh
#define NOISE_GATE_THRESHOLD    800.0f   
#define LOW_PASS_ALPHA          0.45f    
#define HIGH_PASS_ALPHA         0.92f    
#define VOLUME_SCALE            0.85f    
#define NUM_BANDS               8       
#define TEMPORAL_DECAY          0.95f   

static float prevSample = 0.0f;  
static float prev_input_hp = 0.0f;   
static float prev_output_hp = 0.0f;  
static float dynamic_masking_floor[NUM_BANDS] = {0}; 

static int16_t stereo_buffer[CHUNK_SIZE * 2];  
static int16_t pcm_buffer_raw[CHUNK_SIZE]; 
static int16_t pcm_buffer_proc[CHUNK_SIZE]; 
static float processing_block[CHUNK_SIZE];

static i2s_chan_handle_t i2s_tx_chan = NULL; 
static bool wifi_connected = false; 

// --- CÁC HÀM XỬ LÝ DSP NỘI BỘ ---
static inline float noiseGate(float sample) { 
    if (fabsf(sample) < NOISE_GATE_THRESHOLD) return 0.0f;  
    return sample; 
} 
static inline float lowPassFilter(float sample) { 
    float filtered = (LOW_PASS_ALPHA * sample) + ((1.0f - LOW_PASS_ALPHA) * prevSample); 
    prevSample = filtered;  
    return filtered; 
} 
static inline float highPassFilter(float sample) {
    float filtered = HIGH_PASS_ALPHA * (prev_output_hp + sample - prev_input_hp);
    prev_input_hp = sample;
    prev_output_hp = filtered;
    return filtered;
}
void apply_psychoacoustic_masking(float *fft_buffer, size_t length) { 
    size_t samples_per_band = length / NUM_BANDS; 
    if (samples_per_band == 0) return; 
    float band_energy[NUM_BANDS] = {0}; 
    for (size_t b = 0; b < NUM_BANDS; b++) { 
        float sum = 0.0f; 
        for (size_t i = 0; i < samples_per_band; i++) { 
            float val = fft_buffer[b * samples_per_band + i]; 
            sum += val * val; 
        } 
        band_energy[b] = sqrtf(sum / samples_per_band); 
    } 
    for (size_t b = 0; b < NUM_BANDS; b++) { 
        if (dynamic_masking_floor[b] * TEMPORAL_DECAY > band_energy[b]) band_energy[b] = dynamic_masking_floor[b] * TEMPORAL_DECAY; 
        if (band_energy[b] > dynamic_masking_floor[b]) dynamic_masking_floor[b] = band_energy[b]; 
        else dynamic_masking_floor[b] *= TEMPORAL_DECAY; 
    } 
    for (size_t b = 0; b < NUM_BANDS; b++) { 
        if (b > 0 && band_energy[b-1] > band_energy[b] * 4.0f) band_energy[b] = 0.0f; 
        if (b < NUM_BANDS - 1 && band_energy[b+1] > band_energy[b] * 4.0f) band_energy[b] = 0.0f; 
        if (band_energy[b] == 0.0f) { 
            for (size_t i = 0; i < samples_per_band; i++) fft_buffer[b * samples_per_band + i] = 0.0f; 
        } 
    } 
} 

// --- ĐÓNG GÓI JSON VÀ TRUYỀN DỮ LIỆU ĐỒNG BỘ THỜI GIAN THỰC ---
void play_and_stream_udp(void) 
{ 
    size_t bytes_written = 0; 
    uint32_t packet_counter = 0; 

    struct sockaddr_in dest_addr_proc;
    dest_addr_proc.sin_addr.s_addr = inet_addr(SERVER_IP);
    dest_addr_proc.sin_family = AF_INET;
    dest_addr_proc.sin_port = htons(PORT_PROC);

    int sock_proc = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (sock_proc < 0) return;

    // Thiết kế Bộ đệm truyền IoT lớn để chứa cả Audio Payload và chuỗi JSON mở rộng
    static uint8_t tx_iot_buffer[2048];
    size_t audio_payload_size = CHUNK_SIZE * sizeof(int16_t); // 1024 Bytes

    // Tính toán chu kỳ thời gian thực cho FreeRTOS (21.33 ms mỗi khối)
    TickType_t xLastWakeTime = xTaskGetTickCount();
    const TickType_t xBlockPeriod = pdMS_TO_TICKS((CHUNK_SIZE * 1000) / SAMPLE_RATE);

    for (int i = 0; i < audio_len; i += CHUNK_SIZE) { 
        // Ép vòng lặp phải tuân thủ nghiêm ngặt chu kỳ thời gian thực, không cho phép gửi dồn dập
        vTaskDelayUntil(&xLastWakeTime, xBlockPeriod);

        size_t copy_len = (i + CHUNK_SIZE < audio_len) ? CHUNK_SIZE : (audio_len - i); 
        memset(pcm_buffer_raw, 0, sizeof(pcm_buffer_raw)); 
        memcpy(pcm_buffer_raw, &audio_data[i], copy_len * sizeof(int16_t)); 

        int64_t t_start = esp_timer_get_time(); 

        // Khối thực thi DSP
        for (size_t j = 0; j < CHUNK_SIZE; j++) { 
            float current_sample = (float)pcm_buffer_raw[j]; 
            current_sample = highPassFilter(current_sample);
            current_sample = lowPassFilter(current_sample); 
            current_sample = noiseGate(current_sample); 
            processing_block[j] = current_sample; 
        } 

        apply_psychoacoustic_masking(processing_block, CHUNK_SIZE); 

        for (size_t j = 0; j < CHUNK_SIZE; j++) {
            float fsample = processing_block[j] * VOLUME_SCALE; 
            if (fsample > 32767.0f)  fsample = 32767.0f; 
            if (fsample < -32768.0f) fsample = -32768.0f; 
            int16_t final_output = (int16_t)fsample; 
              
            stereo_buffer[j * 2]     = final_output; 
            stereo_buffer[j * 2 + 1] = final_output; 
            pcm_buffer_proc[j]       = final_output; 
        }

        int64_t t_end = esp_timer_get_time(); 
        uint8_t esp_cpu_usage = (uint8_t)(((t_end - t_start) * 100) / 21333);
        if (esp_cpu_usage > 100) esp_cpu_usage = 100;

        // Đẩy âm thanh ra mạch giải mã phần cứng PCM5102A
        i2s_channel_write(i2s_tx_chan, stereo_buffer, CHUNK_SIZE * sizeof(int16_t) * 2, &bytes_written, portMAX_DELAY); 

        // ĐÓNG GÓI GÓI TIN CHUẨN IOT
        // 1. Chèn Sequence Number (4 bytes đầu)
        memcpy(tx_iot_buffer, &packet_counter, 4); 
        // 2. Chèn Audio Payload (1024 bytes tiếp theo)
        memcpy(tx_iot_buffer + 4, pcm_buffer_proc, audio_payload_size); 
        
        // 3. Đóng gói Telemetry JSON ở đuôi gói tin kèm ép kiểu (int) an toàn
        uint32_t free_ram_kb = esp_get_free_heap_size() / 1024;
        uint32_t uptime_sec = esp_timer_get_time() / 1000000;
        
        char json_telemetry[128];
        int json_len = snprintf(json_telemetry, sizeof(json_telemetry), 
                                "{\"cpu\":%d,\"ram\":%d,\"uptime\":%d}", 
                                (int)esp_cpu_usage, (int)free_ram_kb, (int)uptime_sec);

        memcpy(tx_iot_buffer + 4 + audio_payload_size, json_telemetry, json_len);

        // 4. Phát quảng bá gói tin IoT tổng hợp qua Socket mạng Wifi
        sendto(sock_proc, tx_iot_buffer, 4 + audio_payload_size + json_len, 0, 
               (struct sockaddr *)&dest_addr_proc, sizeof(dest_addr_proc)); 
        
        packet_counter++; 
    } 

    sendto(sock_proc, "EOF", 3, 0, (struct sockaddr *)&dest_addr_proc, sizeof(dest_addr_proc)); 

    // ---- ĐOẠN SỬA ĐỂ TRIỆT TIÊU LẶP ÂM CUỐI ----
    // 1. Tạo một block lặng (toàn số 0) để xả sạch bộ đệm DMA
    memset(stereo_buffer, 0, sizeof(stereo_buffer));
    for (int flush = 0; flush < 4; flush++) {
        i2s_channel_write(i2s_tx_chan, stereo_buffer, sizeof(stereo_buffer), &bytes_written, portMAX_DELAY);
    }
    
    // 2. Tắt hẳn kênh phát phần cứng I2S để chip PCM5102A ngắt giải mã
    i2s_channel_disable(i2s_tx_chan);
    // --------------------------------------------

    close(sock_proc); 
} 


// --- CẤU HÌNH WI-FI ---
static void wifi_event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) { 
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) esp_wifi_connect(); 
    else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) { wifi_connected = false; esp_wifi_connect(); } 
    else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) wifi_connected = true; 
} 

void init_wifi(void) { 
    esp_netif_init(); 
    esp_event_loop_create_default(); 
    esp_netif_create_default_wifi_sta(); 
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT(); 
    esp_wifi_init(&cfg); 
    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL); 
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL); 
    wifi_config_t wifi_config = { .sta = { .ssid = WIFI_SSID, .password = WIFI_PASS, .listen_interval = 3 } }; 
    esp_wifi_set_mode(WIFI_MODE_STA); 
    esp_wifi_set_config(WIFI_IF_STA, &wifi_config); 
    esp_wifi_set_ps(WIFI_PS_NONE); 
    esp_wifi_start(); 
} 

void app_main(void)  
{ 
    setvbuf(stdout, NULL, _IOLBF, 0); 
    esp_err_t ret = nvs_flash_init(); 
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) { 
        ESP_ERROR_CHECK(nvs_flash_erase()); 
        ret = nvs_flash_init(); 
    }
    ESP_ERROR_CHECK(ret); 

    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER); 
    chan_cfg.dma_desc_num = 8;    
    chan_cfg.dma_frame_num = 512;  
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, &i2s_tx_chan, NULL)); 

    i2s_std_config_t std_cfg = { 
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE), 
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO), 
        .gpio_cfg = { 
            .mclk = GPIO_NUM_NC, .bclk = I2S_BCK_IO, .ws = I2S_WS_IO, .dout = I2S_DO_IO, .din = GPIO_NUM_NC, 
        } 
    }; 
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(i2s_tx_chan, &std_cfg)); 
    ESP_ERROR_CHECK(i2s_channel_enable(i2s_tx_chan)); 

    init_wifi(); 
    while (!wifi_connected) { vTaskDelay(pdMS_TO_TICKS(100)); }
    vTaskDelay(pdMS_TO_TICKS(1000)); 

    // CHẠY 1 LẦN DUY NHẤT KHI KHỞI ĐỘNG
    play_and_stream_udp(); 

    // Vòng lặp giữ luồng (Idling Loop) để tránh sập hệ thống
    while (true) { 
        vTaskDelay(pdMS_TO_TICKS(1000)); 
    } 
}
