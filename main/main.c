#include <string.h>  
#include <stdio.h>   
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/i2s_std.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "audio_data.h" 

static const char *TAG = "esp32_audio";

#define I2S_BCK_IO      GPIO_NUM_26
#define I2S_WS_IO       GPIO_NUM_25
#define I2S_DO_IO       GPIO_NUM_22
#define SAMPLE_RATE     24000
#define CHUNK_SIZE      512

// ====================================================================
// CẤU HÌNH BỘ LỌC DSP CHỐNG NHIỄU 
// ====================================================================
#define ENABLE_NOISE_GATE      1       
#define NOISE_GATE_THRESHOLD   2000    

#define ENABLE_LOW_PASS_FILTER 1       
#define LOW_PASS_ALPHA         0.25f   

#define VOLUME_SCALE           1.8f    

#define ENABLE_REAL_TIME_METRICS 1
#define MONITOR_INTERVAL         25

static float prevSample = 0.0f; 
static int16_t stereo_buffer[CHUNK_SIZE * 2]; 
static int16_t pcm_buffer[CHUNK_SIZE];
static i2s_chan_handle_t i2s_tx_chan = NULL;

static int16_t noiseGate(int16_t sample) {
#if ENABLE_NOISE_GATE
    int16_t absVal = (sample >= 0) ? sample : -sample;
    if (absVal < NOISE_GATE_THRESHOLD) return 0; 
#endif
    return sample;
}

static int16_t lowPassFilter(int16_t sample) {
#if ENABLE_LOW_PASS_FILTER
    float filtered = (LOW_PASS_ALPHA * (float)sample) + ((1.0f - LOW_PASS_ALPHA) * prevSample);
    prevSample = filtered; 
    return (int16_t)filtered;
#else
    return sample;
#endif
}

static void playAudio(void)
{
    size_t bytes_written = 0;
    uint32_t chunk_count = 0;

    ESP_LOGI(TAG, "[REAL-TIME MONITORING]");
    ESP_LOGI(TAG, "Progress | Free Heap | CPU Load");
    ESP_LOGI(TAG, "----------------------------------");

    prevSample = 0.0f; // Reset trạng thái bộ lộc về mức cân bằng 0

    // Gửi tag mở đầu ngầm cho Script Python bắt tín hiệu
    printf("\n---START_DATA_CSV---\n");
    printf("Sample_ID,Original,Filtered,Final\n");

    for (int i = 0; i < audio_len; i += CHUNK_SIZE) {
        int64_t chunk_start = esp_timer_get_time();
        size_t copy_len = (i + CHUNK_SIZE < audio_len) ? CHUNK_SIZE : (audio_len - i);

        memcpy(pcm_buffer, &audio_data[i], copy_len * sizeof(int16_t));

        for (size_t j = 0; j < copy_len; j++) {
            int16_t orig_sample = pcm_buffer[j];
            
            // SỬA: Thực hiện lọc DSP trên tín hiệu đơn kênh gốc trước để tránh lặp bộ đệm
            int16_t filtered_sample = noiseGate(orig_sample);
            filtered_sample = lowPassFilter(filtered_sample);
            
            // Tăng âm lượng và Giới hạn biên độ tín hiệu (Clipping chống tràn số)
            float fsample = (float)filtered_sample * VOLUME_SCALE;
            if (fsample > 32767.0f) fsample = 32767.0f;
            if (fsample < -32768.0f) fsample = -32768.0f;
            int16_t final_output = (int16_t)fsample;
            
            // Nhân bản tín hiệu sạch ra 2 kênh của bộ đệm Stereo phần cứng
            stereo_buffer[j * 2]     = final_output; // Kênh trái
            stereo_buffer[j * 2 + 1] = final_output; // Kênh phải

            // Ghi ngầm dữ liệu 2000 mẫu đầu vào luồng ra file (Không in text ra Terminal)
            if ((i + j) < 2000) {
                fprintf(stdout, "%d,%d,%d,%d\n", (int)(i + j), orig_sample, filtered_sample, final_output);
                if ((i + j) == 1999) {
                    printf("---END_DATA_CSV---\n");
                    fflush(stdout); // Ép giải phóng bộ đệm ghi file ngay tức khắc
                }
            }
        }

        // Đẩy dữ liệu âm thanh Stereo cân bằng trục vào DMA phần cứng I2S
        ESP_ERROR_CHECK(i2s_channel_write(i2s_tx_chan, stereo_buffer, copy_len * sizeof(int16_t) * 2, &bytes_written, portMAX_DELAY));

#if ENABLE_REAL_TIME_METRICS
        uint32_t chunk_time_us = (uint32_t)(esp_timer_get_time() - chunk_start);
        uint32_t heap_now = esp_get_free_heap_size();
        float expected_chunk_time_ms = (float)CHUNK_SIZE / SAMPLE_RATE * 1000.0f;
        float cpu_load = ((float)chunk_time_us / 1000.0f) / expected_chunk_time_ms * 100.0f;
        chunk_count++;

        if ((chunk_count % MONITOR_INTERVAL == 0) || (i + CHUNK_SIZE >= audio_len)) {
            float progress = ((float)i / audio_len) * 100.0f;
            ESP_LOGI(TAG, "%6.1f%% | %8zu KB | %7.1f%%", progress, (size_t)(heap_now / 1024), cpu_load);
        }
#endif
    }

    // Đóng tag an toàn phòng hờ trường hợp bài hát ngắn hơn 2000 mẫu
    printf("---END_DATA_CSV---\n");
    fflush(stdout);

    ESP_ERROR_CHECK(i2s_channel_disable(i2s_tx_chan));
}

void app_main(void) 
{
    // Cấu hình stdout luồng dòng lệnh xả trực tiếp dữ liệu theo dòng (Hủy Block Buffering)
    setvbuf(stdout, NULL, _IOLBF, 0);

    ESP_LOGI(TAG, "ESP32 Audio Stereo System Init");
    vTaskDelay(pdMS_TO_TICKS(500));

    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = 32;   
    chan_cfg.dma_frame_num = 512; 
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, &i2s_tx_chan, NULL));

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = GPIO_NUM_NC, .bclk = I2S_BCK_IO, .ws = I2S_WS_IO, .dout = I2S_DO_IO, .din = GPIO_NUM_NC,
            .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false}
        }
    };

    ESP_ERROR_CHECK(i2s_channel_init_std_mode(i2s_tx_chan, &std_cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(i2s_tx_chan));

    ESP_LOGI(TAG, "[PLAYING STEREO AUDIO]");
    playAudio();

    // In từ khóa kích hoạt ngầm lệnh thực thi Audacity trên Ubuntu PC
    printf("\n---PC_TRIGGER_AUDACITY---\n");
    fflush(stdout);
    
    ESP_LOGI(TAG, "[PLAYBACK COMPLETE]");

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}