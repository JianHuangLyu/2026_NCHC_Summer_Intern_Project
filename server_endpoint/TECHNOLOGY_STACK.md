# Technology Stack

本文件說明 PATHOVISION Analysis System 的實際技術組成、元件責任與資料流。根目錄 README 提供安裝與操作流程；目錄責任與產物交接請見 [docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md)。



> 圖片為技術分類總覽；下表依目前程式碼補充 YOLO11s、vLLM、Pydantic、Pillow、JSON Schema 與多 ROI 平行推論等最新實作。

## 技術總覽

| 分類 | 項目 | 技術 | 說明 |
|---|---|---|---|
| **前端** | 框架與函式庫 | Gradio | 建立操作介面，提供影像上傳、模型推論、分析結果與互動操作 |
| **前端** | 樣式工具 | CSS | 負責介面排版、樣式與整體視覺設計 |
| **後端** | 程式語言 | Python | 負責系統主要邏輯、資料處理、模型流程控制與服務整合 |
| **後端** | AI / ML Framework | PyTorch | 提供深度學習模型執行、YOLO 訓練與推論及 VLM 推論能力 |
| **後端** | AI Inference Service | vLLM | 提供大型 VLM 高效 GPU 推論服務，支援批次推論與 OpenAI-compatible API |
| **後端** | Web API | FastAPI + Uvicorn | 使用 FastAPI 建立 RESTful API，用於處理模型資訊、影像分析病例與報告管理 |
| **後端** | AI Models | YOLO11m、YOLO11s、Gemma4、Mistral Small 3.1 | YOLO 負責異常區域定位；VLM 用於 ROI 影像分析與結構化報告生成 |
| **Prompt工程** | Skill / Prompt Optimization | Microsoft SkillOpt | 透過 Teacher–Student 評估與迭代方式最佳化 Skill 及 Prompt，使 Student Model 結構化輸出逐步近似 Teacher Model |
| **基礎架構與部署** | 運算與儲存 | NCHC_NANO4 (NVIDIA H200) + HFS | 提供模型訓練、推論及 Skill Optimization 所需 HPC 與 GPU 運算能力，以及透過 HFS 進行資料處理及管理 |
| **基礎架構與部署** | 資源調度 | Slurm | 負責 HPC 之 GPU、CPU、記憶體及執行時間等資源配置 |
| **基礎架構與部署** | 遠端連線 | OpenSSH、SOCKS5h | 登入 HPC 並透過 Port Forwarding 存取遠端系統服務 |
| **基礎架構與部署** | 執行環境 | Python venv | 提供系統執行與 Python 套件隔離環境 |
| **基礎架構與部署** | 部署架構 | Client-Server | 透過 Client-Server 架構，使客戶端達成輕量級部署，伺服器端負責儲存模型推論記錄及提供高速運算資源進行推論服務 |

每個結構化模型擁有自己的 `best_prompt/` 與 `best_skills/`。FastAPI 只有在權重、Prompt、Schema、Skill Registry、Registry 指定 Skills 與 vLLM endpoint 全部就緒時，才將模型標示為 `inference_ready=true`。


## 推論與載入最佳化

- Gemma、Mistral 與 YOLO 分配至不同 GPU，避免模型互相搶占顯存。
- Gemma 與 Mistral 服務平行啟動。
- `PATHOVISION_VLLM_SKIP_MM_PROFILING=1` 減少多模態啟動 Profiling。
- vLLM 使用 Prefix Caching、CUDA Graph／Compilation、Safetensors Prefetch 與 `performance-mode=interactivity`。
- `max-num-seqs=2` 配合同模型兩個 API in-flight requests，讓獨立 ROI 可動態批次處理。
- YOLO 在背景預載，GPU 模式預設使用 FP16。
- ROI 在送入分析推論模型前限制最大邊長與 Pixel 數，兼顧視覺 Token、延遲與顯存。
- Prompt／Skill Bundle 在 Server Process 中快取，避免每次重讀完整控制檔。

## 通訊與安全

```text
Browser / localhost
        │
        ▼
Gradio Client ── Windows OpenSSH / SOCKS5h ──► NANO4 Compute Node FastAPI
                                                        │
                                      127.0.0.1-only vLLM endpoints
```

- NANO4 內部 vLLM Endpoint 綁定 `127.0.0.1`。
- REST 請求使用 `X-API-Key`。
- SSH 密碼與 2FA 不放入命令列。
- Client 關閉時會取消自己提交的 Slurm Job。
- Runtime 檔案採 `0600`，目錄採 `0700`。

## 版本來源

Python 套件版本以以下檔案為準：

- Server：[requirements.txt](requirements.txt)
- Windows Client：[client_endpoint/requirements.txt](../client_endpoint/requirements.txt)
- vLLM：由 `PATHOVISION_VLLM_BIN` 指向的 NANO4 Runtime 決定。

## 醫療使用邊界

本專案僅整理醫學影像中直接可見的形態特徵。YOLO 類別、信心分數、分析推論模型描述及結構化報告均須由合格專業人員複核，不得單獨用於疾病診斷、治療決策或取代正式病理報告，仍需要有專業人士在旁驗證!
