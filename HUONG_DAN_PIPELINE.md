Hướng dẫn cài đặt và chạy DataCrawl Pipeline
Tài liệu hướng dẫn cài đặt môi trường, cấu hình và chạy pipeline thu thập trend (YouTube + TikTok), lưu link/nội dung vào data_trends/.

1. Yêu cầu hệ thống
Thành phần	Ghi chú
Python
3.10+ (khuyến nghị 3.11 hoặc 3.12)
Git
Clone repo (tuỳ chọn)
Mạng
Cần internet để scrape YouTube/TikTok và search web
GPU NVIDIA
Tuỳ chọn — chỉ cần khi chạy mode=full (Whisper transcript)
Windows: PowerShell
macOS/Linux: có script setup.sh

2. Cài đặt (Windows)
Mở PowerShell tại thư mục project (ví dụ G:\DEV\CrawlData):

cd G:\DEV\CrawlData
# Tạo virtual environment (bắt buộc nếu chưa có .venv)
python -m venv .venv
# hoặc: py -3 -m venv .venv
# Kích hoạt venv
.\.venv\Scripts\Activate.ps1
Nếu báo lỗi running scripts is disabled:

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
Khi thành công, prompt có tiền tố (.venv).

python -m pip install --upgrade pip
pip install -r requirements.txt
# Trình duyệt cho scrape TikTok
python -m playwright install chromium
2.1. File cấu hình .env
copy .env.example .env
Chỉnh .env:

# Để trống = chỉ dùng web search (DuckDuckGo), không gọi OpenAI
OPENAI_API_KEY=
# Tuỳ chọn — search chất lượng hơn
TAVILY_API_KEY=
# Whisper (chế độ full)
WHISPER_DEVICE=auto
WHISPER_MODEL=base
Biến	Mô tả
OPENAI_API_KEY
Tóm tắt trend bằng GPT; trống vẫn chạy được
TAVILY_API_KEY
Search API thay DuckDuckGo
WHISPER_DEVICE
auto | cuda | cpu
WHISPER_MODEL
tiny, base, small, …
2.2. GPU cho Whisper (tuỳ chọn)
Nếu có card NVIDIA và muốn transcript nhanh:

```powershell
.\install_gpu.ps1
```

Script tự chọn wheel CUDA theo Python:
- **Python 3.13+** → CUDA 12.4 (`cu124`) — index `cu121` không có wheel cho 3.13
- **Python 3.12 trở xuống** → CUDA 12.1 (`cu121`)

Kiểm tra driver: `nvidia-smi`

3. Cài đặt (macOS / Linux)
cd /path/to/CrawlData
chmod +x setup.sh
./setup.sh
source .venv/bin/activate
Script tự: tạo .venv, cài requirements.txt, playwright install chromium, copy .env từ .env.example.

4. Pipeline làm gì?
Orchestration bằng Prefect (main_pipeline.py):

Từ khóa → Tạo thư mục data_trends → Scrape YouTube + TikTok
       → Lưu link video → AI/search tóm tắt trend
       → (tuỳ chọn) Tải video + Whisper transcript
       → pipeline_summary.json + trends_index.json
4.1. Hai chế độ chạy
Chế độ	Cách bật	Mô tả
links (mặc định)
Không có --full
Link video + trend_info.txt + trend_content.json — không tải file video
full
Thêm --full
Thêm tải 1 video đầu + transcript.txt (Whisper)
4.2. Bộ lọc video (mặc định)

| Tiêu chí | Giá trị mặc định |
|----------|------------------|
| Lượt xem tối thiểu | **≥ 500.000** (YouTube + TikTok) |
| Thời gian đăng | **7 ngày** gần nhất (UI/CLI: chọn **30 ngày**) |
| Số video tối đa | **100** (sắp xếp theo lượt xem giảm dần) |

Cấu hình thêm trong `.env`: `MIN_VIEW_COUNT`, `RECENCY_DAYS`, `VIDEO_LIMIT`.

4.3. Nguồn dữ liệu

- **YouTube:** yt-dlp search (nhiều query + `dateafter`)
- **TikTok:** Playwright discover (search/video + tag) + yt-dlp lấy views/ngày đăng

Prefect lưu metadata cục bộ tại `.prefect/` (SQLite).

5. Chạy pipeline
Đảm bảo đã activate venv và đứng tại root project.

5.1. CLI
Chế độ nhanh (link + text):

python main_pipeline.py "AI tools"
Chế độ đầy đủ (tải video + transcript):

python main_pipeline.py "AI agent" --full
python main_pipeline.py "AI agent" --month
Tham số 1: từ khóa (mặc định "AI tools" nếu bỏ qua)
`--full`: bật download + Whisper
`--month`: lọc video trong **30 ngày** (mặc định **7 ngày**)
Ví dụ từ khóa có khoảng trắng:

python main_pipeline.py "ChatGPT automation"
Kết quả in ra console (dict JSON) và ghi log.

5.2. Giao diện Streamlit
streamlit run app.py
Trình duyệt mở dashboard DataCrawl:

Trang	Chức năng
Kho lưu trữ
Xem trend đã lưu, video, text + link
Thu thập mới
Nhập keyword, chọn links / full, chạy pipeline
Nhật ký
Xem logs/pipeline.log
6. Kết quả lưu ở đâu?
Mỗi lần chạy tạo (hoặc tiếp tục) thư mục:

data_trends/<YYYY-MM-DD>_Topic_<keyword_sanitized>/
├── Videos/
│   ├── video_links.txt
│   ├── <video_id>.mp4        # mode=full
│   └── clips/<video_id>/     # minute_01.mp4, minute_02.mp4, ...
├── Content/
│   └── <video_id>.json       # nội dung theo phút (noi_dung_theo_phut)
├── Images/
├── video_links.json
├── video_links.md
├── trend_info.txt
├── trend_content.json
├── pipeline_summary.json
└── transcript.txt          # chỉ khi mode=full
Index tổng: data_trends/trends_index.json
Log: logs/pipeline.log

7. Cấu trúc project (tóm tắt)
CrawlData/
├── main_pipeline.py      # Entry CLI + Prefect flow
├── app.py                # Streamlit UI
├── requirements.txt
├── .env.example / .env
├── install_gpu.ps1       # PyTorch CUDA (Windows)
├── setup.sh              # Setup macOS/Linux
├── src/
│   ├── trend_scraper.py  # YouTube + TikTok
│   ├── context_agent.py  # Tóm tắt trend (OpenAI / search)
│   ├── content_store.py  # Lưu link
│   ├── video_minute_splitter.py  # JSON theo phút + cắt clip (mode=full)
│   ├── audio_transcriber.py      # faster-whisper
│   ├── video_downloader.py
│   └── ...
├── data_trends/          # Output
└── logs/
8. Xử lý lỗi thường gặp
Activate.ps1 không tồn tại
Chưa tạo venv:

python -m venv .venv
.\.venv\Scripts\Activate.ps1
Playwright / TikTok lỗi
python -m playwright install chromium
TikTok có thể chặn bot — pipeline vẫn chạy nếu YouTube có kết quả; xem logs/pipeline.log.

No trending videos found for keyword
Đổi từ khóa, kiểm tra mạng, thử lại sau vài phút.

OpenAI / API key
Để trống OPENAI_API_KEY → chế độ search-only. Key sai → warning trong log, vẫn có trend_info.txt từ search.

Whisper chậm / hết RAM
Dùng WHISPER_MODEL=tiny hoặc base trong .env
Chạy links thay vì full
Cài CUDA: .\install_gpu.ps1
Thư mục trend đã tồn tại cùng ngày + keyword
StorageManager resume thư mục cũ (không ghi đè). Muốn chạy “sạch” → đổi keyword hoặc xóa/đổi tên folder trong data_trends/.

Prefect SQLite lock (Windows)
Project đã cấu hình .prefect/ local qua src/prefect_bootstrap.py. Nếu vẫn lỗi, đóng process Streamlit/CLI khác đang chạy pipeline.

9. Lệnh tham khảo nhanh
cd G:\DEV\CrawlData
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env
# Chạy nhanh
python main_pipeline.py "AI tools"
# Chạy full + transcript
python main_pipeline.py "AI tools" --full
# UI
streamlit run app.py
10. Cập nhật dữ liệu sau khi đổi bộ lọc

Kho **Kho lưu trữ** có thể vẫn hiển thị lần chạy cũ (ví dụ 18 video, nhiều video dưới 500k).
Vào **Thu thập mới**, chọn **30 ngày** nếu 7 ngày ra quá ít video, rồi chạy lại pipeline để ghi đè `video_links.json`.

Nếu không đủ 100 video: đây là bình thường — chỉ video thật sự đạt ngưỡng mới được lưu.

11. Hỗ trợ
Log chi tiết: logs/pipeline.log
Prefect UI (tuỳ chọn): prefect server start rồi xem flow run tại local server
Tài liệu áp dụng cho repo DataCrawl — pipeline AI content / trend discovery.