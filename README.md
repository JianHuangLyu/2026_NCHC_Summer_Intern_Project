# Structured Pathology Image Analysis via YOLO-Based Abnormality Detection and Teacher-Guided Student Skill Optimization

病理影像異常區域定位與結構化分析系統 結合 YOLO 異常區域定位、教師模型引導的技能／提示詞最佳化，以及學生多模態模型的結構化推論。系統先在病理影像中找出候選異常區域，再由使用者選擇真正要分析的 ROI，最後以固定 JSON Schema 產生逐區域、可追溯且可保存的形態學報告

專案採用兩端點交付：Windows 使用者端放在 `client_endpoint/`；NCHC NANO4 的 API、模型、Slurm 與個案資料全部集中在 `server_endpoint/`

> [!CAUTION]
> 本系統是研究用途的非診斷性病理影像形態輔助工具。YOLO 框選、模型文字與結構化報告都必須由合格專業人員複核，不得取代病理醫師判讀、臨床資訊整合、正式病理報告或醫療決策

<br>

## 專案資訊欄🪪

- Project Author：Jian Huang Lyu
- Email: a7929771@gmail.com
- [Demo](https://youtu.be/1BliE2S8V1c)
- [Presentation](Intern%20Presentation/NCHC_Intern_Presentation.pptx)

<br>

## 動機🔬

病理醫學影像異常區域準確定位與分析是影像判讀及臨床診斷重要基礎。傳統病理影像仰賴醫師逐一閱片標註異常區域及撰寫分析報告，不僅耗費大量時間且易出錯，因此本專案提出基於病理影像異常區域偵測與教師引導學生技能最佳化之結構化分析方法，以利於後續改善病理影像判讀及分析之流程效率

<br>

## 方法及實驗📚

![方法架構圖](server_endpoint/docs/assets/Model.png)

### 一、資料前處理

本專案以 RefPath 病理視覺定位資料為想法去延伸。原始資料提供病理影像、框選區域的病理描述描述及 bounding box座標；本專案再額外整理成 YOLO可接受的格式再進行定位模型訓練與評估所需記錄。所列的專案處理後切分如下：

| 資料 | 記錄數 | 比例 |
|---|---:|---:|
| 訓練集 | 106,076 | 79.70% |
| 驗證集 | 13,556 | 10.19% |
| 測試集 | 13,464 | 10.12% |
| 合計 | 133,096 | 100% |

涵蓋內容包括肺癌、乳癌、腎癌與淋巴結轉移相關病理影像。上述數量是本專案經data augmentation與整理後的記錄數，不等同於 RefPath 官方公布的原始資料數目

<br>

### 二、YOLO 異常區域定位

在一致的資料與實驗設定下比較 9 個 YOLO 變體，並以 Precision、Recall、mAP 與 F1 score 評估!

#### 不同YOLO模型實驗結果

| 模型 | 參數量（M） | Precision | Recall | mAP50 | mAP50–95 | F1 score |
|---|---:|---:|---:|---:|---:|---:|
| YOLOv8n | 0.31 | 68.6% | 77.6% | 77.8% | 63.8% | 0.728 |
| YOLOv8s | 11.2 | 66.7% | 78.0% | 73.7% | 61.2% | 0.727 |
| YOLOv8m | 25.9 | 74.0% | 79.6% | 81.8% | 68.8% | 0.767 |
| YOLO11n | 2.6 | 74.9% | 79.5% | 84.4% | 68.8% | 0.771 |
| **YOLO11s** | **9.5** | **77.2%** | <ins>80.3%</ins> | <ins>86.5%</ins> | <ins>71.8%</ins> | <ins>0.787</ins> |
| **YOLO11m** | **20.1** | <ins>76.8%</ins> | **81.7%** | **86.6%** | **72.6%** | **0.792** |
| YOLO26n | 2.4 | 74.9% | 78.4% | 83.9% | 68.2% | 0.766 |
| YOLO26s | 9.5 | 76.0% | 79.8% | 85.2% | 70.1% | 0.779 |
| YOLO26m | 20.4 | 72.1% | 77.4% | 80.5% | 64.8% | 0.746 |

YOLO11m 在 Recall、mAP50、mAP50–95 與 F1 score 表現最佳；YOLO11s 具有最高 Precision，且以較少參數維持接近 YOLO11m 的效能。因此部署端保留兩種選擇：YOLO11s 偏向速度與資源效率，YOLO11m 偏向整體定位效果

**Note: 粗體字代表最佳效能, 底線部分代表效能表現屬整體次佳者**

<br>

### 三、教師引導的學生模型技能最佳化

使用雲端醫療特化專用的 MedGemma 1.5 作為教師模型，提供結構化輸出參考；再以地端通用VLM學生模型 Mistral Small 3.1 與 Gemma4 進行比較。最佳化時不更新學生模型權重(fine tune)，而是把 Prompt 與 Skills 視為可訓練的外部文字參數，透過 SkillOpt 反覆評分與更新。部署端使用經審查的 `best_prompt/`、`best_skills/` 與凍結的學生模型權重

Soft Score 綜合評估以下面向：

- JSON Schema 合法性
- 欄位值與教師參考的 Token F1 相似度
- 狀態欄位正確性
- 描述與可見影像證據的一致性
- 摘要一致性
- 禁止性輸出避免能力，例如不應直接輸出診斷或惡性判定

<br>

#### SkillOpt實驗結果

| 學生模型 | 最佳化對象 | Soft Score | Schema 合法率 | 禁止性輸出案例數 |
|---|---|---:|---:|---:|
| Mistral Small 3.1 | Skills | 0.0543 → 0.2685 | 10.22% → 83.33% | 12,088 → 2,245 |
| Mistral Small 3.1 | Prompt | 0.0190 → 0.1942 | 10.83% → 99.29% | 12,006 → 95 |
| Gemma4 | Skills | 0.4272 → 0.4300 | 95.68% → 95.94% | 581 → 546 |
| Gemma4 | Prompt | 0.1670 → 0.1691 | 95.35% → 97.34% | 626 → 358 |

Mistral 的輸出格式與禁止性內容改善幅度最明顯；Gemma4 的baseline Schema 合法率表現不錯，因此增益較小。欄位內容相似度並非所有設定都同步上升，顯示「格式更正確」不等於「內容一定更接近教師」，仍需專業人員進行後續驗證

<details>
<summary>查看欄位內容相似度完整結果</summary>

| 學生模型 | 最佳化對象 | 最佳化前 | 最佳化後 | 變化 |
|---|---|---:|---:|---:|
| Mistral Small 3.1 | Skills | 20.38% | 18.43% | -1.95 個百分點 |
| Mistral Small 3.1 | Prompt | 19.50% | 27.09% | +7.59 個百分點 |
| Gemma4 | Skills | 33.76% | 33.98% | +0.22 個百分點 |
| Gemma4 | Prompt | 25.14% | 25.09% | -0.05 個百分點 |

</details>

<br>

## 系統功能🖥️

- 可選擇 YOLO11s 或 YOLO11m 進行異常區域定位
- YOLO 無偵測結果時不啟動學生模型，避免無目標推論
- 同一影像可勾選多個 ROI，且只分析被選取的區域 (多個區域需分析，採平行處理方式)
- Gemma4 與 Mistral Small 3.1 可分別分析相同 ROI，報告互不覆蓋
- 每份輸出套用模型專屬 Prompt、Skill Registry、Skills 與 JSON Schema
- 報告頁可獨立切換模型與 ROI，結果以病理專業繁體中文呈現
- 個案紀錄支援新增、載入、欄位修改、個案編號修改與整筆刪除
- Server 保存原圖、定位圖、ROI、個案 metadata 與模型 × ROI 報告
- Client 可提交及取消 Slurm Job，並輪詢(polling)模型載入的進度

<br>

## Client-Server部署架構🌏

```mermaid
flowchart LR
    subgraph W[Windows 本機]
        U[瀏覽器] --> C[client_endpoint\nGradio 使用者端]
        C --> S[Windows OpenSSH\n密碼與二階段驗證]
    end

    S --> L[NANO4 登入節點]
    L -->|提交與管理工作| Q[Slurm]
    C -->|SOCKS5h 加密通道| A[FastAPI REST\nserver_endpoint]

    subgraph N[NANO4 計算節點]
        Q --> G0[GPU 0\nGemma4 31B／vLLM]
        Q --> G1[GPU 1\nMistral Small 3.1 24B／vLLM]
        Q --> G2[GPU 2\nFastAPI／YOLO11s／YOLO11m]
        A --> G0
        A --> G1
        A --> G2
        A --> H[(HFS\n影像、ROI、JSON 報告)]
    end
```

<br>

### 部署邊界🚧

| 端點 | 放置位置 | 內容 | 不應放置的內容 |
|---|---|---|---|
| `client_endpoint/` | Windows localhost | Gradio UI、REST Client、OpenSSH／SOCKS5h、Slurm 管理 | 模型權重、病理影像、Server 個案資料 |
| `server_endpoint/` | NCHC NANO4 的 `/work/<USER>/PathoVision_Server` | FastAPI、YOLO、學生模型、vLLM 啟動、Slurm、個案資料; 同時`server_endpoint`放置於NANO4裡, 資料夾需重新命名成`PathoVision_Server` | SSH 密碼、OTP、提交至 Git 的 token |

Client 使用互動式 OpenSSH 完成密碼與二階段驗證，並透過同一工作階段提交 Slurm。實際 REST 流量經 localhost SOCKS5h 代理送到計算節點；vLLM 只綁定計算節點的 `127.0.0.1`，不直接暴露到外部網路。

<br>

## 專案結構📑

```text
Project/
├── client_endpoint/                 # 可獨立放到 Windows 的使用者端
│   ├── app.py
│   ├── api_client.py
│   ├── nchc_remote.py
│   ├── mcp_server.py
│   └── requirements.txt
├── server_endpoint/                 # 可獨立部署到 NCHC 的伺服器端
│   ├── pathovision_server.py        # FastAPI、YOLO、個案與報告 API
│   ├── student_vlm.py               # ROI、Prompt／Skill 與 vLLM 整合
│   ├── Localization_model/          # YOLO 說明與另行交付的權重
│   ├── Student_model/               # 學生模型控制檔與下載位置
│   ├── slurm/                       # 一張 Job 啟動三 GPU 服務
│   ├── scripts/                     # 模型安裝、驗證與 API key 工具
│   ├── tests/                       # Server、Schema、ROI 與持久化測試
│   ├── docs/                        # 維運與交接文件
│   ├── requirements.txt
│   └── TECHNOLOGY_STACK.md
├── Intern Presentation/
│   └── NCHC_Intern_Presentation.pptx # 實習成果簡報
├── .github/workflows/ci.yml
└── README.md
```

<br>

### YOLO 權重

YOLO11s 與 YOLO11m 另行打包為：

```text
2026_NCHC_Summer_Intern_Project_YOLO_weights.zip
└── Localization_model/
    ├── yolo11s_best.pt
    └── yolo11m_best.pt
```

壓縮包 SHA-256：

```text
7731db12b1c3fcdb39fe036772e0b69ab851ce8c80570626da85c5d42737a000
```

權重包不在 GitHub 中，可從 [Google Drive 下載 YOLO 權重包](https://drive.google.com/file/d/14_QVjcctgqczYHWX9Ed3NhP7TnuASK_o/view?usp=sharing)。個別權重雜湊與驗證方式請見 [`server_endpoint/Localization_model/README.md`](server_endpoint/Localization_model/README.md)

<br>

### 學生多模態模型一鍵安裝

學生模型由 Hugging Face 自動下載；兩個模型約 160 GB，建議預留 180 GB。下載已整合到下方 Server 快速安裝，單模型與固定 revision 選項請見 [`server_endpoint/Student_model/README.md`](server_endpoint/Student_model/README.md)

<br>

## 快速安裝📩

### 一、NCHC Server

```bash
git clone https://github.com/ChienHaungLu/2026_NCHC_Summer_Intern_Project.git
cd 2026_NCHC_Summer_Intern_Project/server_endpoint

# 解壓另行取得、放在 repo 根目錄的 YOLO 權重
unzip ../2026_NCHC_Summer_Intern_Project_YOLO_weights.zip -d .
# 安裝 Server 依賴
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

# 下載學生模型並驗證所有權重
python3 -m pip install --user --upgrade huggingface_hub
hf auth login
./scripts/install_student_vlm.sh
python3 scripts/verify_model_assets.py --include-yolo
```

提交 Slurm 前指定獨立 vLLM 環境的執行檔：

```bash
export PATHOVISION_VLLM_BIN=/absolute/path/to/vllm
```

<br>

### 二、Windows Client

在 Windows PowerShell 執行：

```powershell
cd client_endpoint
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

不使用選配 MCP 時改以 `.venv\Scripts\python app.py --no-mcp` 啟動。預設介面網址為 `http://127.0.0.1:8200`

<br>

## 啟動與操作🧑‍💻

### 建議方式：由 Client 自動配置

1. 在 Windows 啟動 `client_endpoint/app.py`
2. 輸入 NANO4 帳號、密碼並完成二階段驗證
3. 選擇 `/work/<USER>/2026_NCHC_Summer_Intern_Project/server_endpoint`
4. 選擇 Slurm partition、account 與資源後提交工作
5. Job 進入 `RUNNING` 後即可進入分析頁；模型載入狀態會每兩秒更新
6. 上傳原始影像並選擇 YOLO11s 或 YOLO11m
7. 勾選要分析的 ROI，再選擇 Gemma4 或 Mistral Small 3.1
8. 到結構化報告頁查看模型 × ROI 報告，或在個案紀錄頁管理資料
9. 正常關閉 Client 或按下結束工作階段，歸還 Slurm 資源

<br>

### 手動提交三 GPU Stack

```bash
cd /work/<USER>/2026_NCHC_Summer_Intern_Project/server_endpoint
export PATHOVISION_VLLM_BIN=/absolute/path/to/vllm
sbatch --account=<wallet-id> slurm/pathovision_vlm_stack.sbatch
```

<br>

預設資源分配：

| GPU | 服務 | 網路可見性 |
|---:|---|---|
| 0 | Gemma4 31B vLLM | 僅 `127.0.0.1` |
| 1 | Mistral Small 3.1 24B vLLM | 僅 `127.0.0.1` |
| 2 | FastAPI、YOLO11s、YOLO11m | 經 SOCKS5h 由 Client 存取 |

<br>

## 推論與保存流程⚓

1. Client 上傳未標註的原始影像
2. Server 用指定 YOLO 定位候選區域並建立個案
3. 使用者勾選一個或多個 ROI；沒有偵測或沒有勾選時流程停止
4. Server 從保存的乾淨原圖裁切 ROI，不使用畫過框的預覽圖
5. 每個 ROI 獨立送入所選學生模型，同模型預設最多兩個請求並行
6. 推論套用模型專屬 Prompt、Skill Registry、Skills 與 JSON Schema
7. 每個模型 × ROI 的輸出獨立驗證與保存，單區域失敗不會覆蓋其他成功報告

<br>

```text
.pathovision_server/cases/PV-.../
├── original.png
├── localized.png
├── roi_001.png
├── roi_002.png
├── student_vlm_gemma4_region_001.json
├── student_vlm_mistral-small-3.1_region_001.json
└── analysis.json
```

<br>

## 主要環境變數⚙️

| 變數 | 預設值或用途 |
|---|---|
| `PATHOVISION_PROJECT_DIR` | `server_endpoint/` 的絕對路徑 |
| `PATHOVISION_SERVER_PYTHON` | Server `.venv/bin/python` |
| `PATHOVISION_VLLM_BIN` | vLLM 執行檔絕對路徑，Slurm 必填 |
| `PATHOVISION_MODEL_PATH` | `Localization_model/yolo11m_best.pt` |
| `PATHOVISION_STUDENT_MODEL_ROOT` | `Student_model/` |
| `PATHOVISION_CASE_ROOT` | `.pathovision_server/cases` |
| `PATHOVISION_API_KEY` | REST 的 `X-API-Key`；自動模式會隨機產生 |
| `PATHOVISION_MAX_VLM_ROIS` | 單次最多 ROI，預設 4 |
| `PATHOVISION_VLM_MAX_CONCURRENT_PER_MODEL` | 每模型並行請求數，預設 2 |
| `PATHOVISION_VLLM_MAX_MODEL_LEN` | vLLM context 上限，預設 49,152 |
| `PATHOVISION_YOLO_HALF` | GPU YOLO 使用 FP16，預設 1 |

完整設定請參考 [`server_endpoint/.env.example`](server_endpoint/.env.example)

<br>

## 測試📋

```bash
# Server、Schema、ROI 與資料持久化
cd server_endpoint
.venv/bin/python -m unittest discover -v tests

# Slurm 語法
bash -n slurm/pathovision_vlm_stack.sbatch slurm/pathovision_api.sbatch.example

# Client 邏輯
cd ../client_endpoint
python -m unittest discover -v . "test_*.py"
```

GitHub Actions 會在 push 與 pull request 時執行相同的依賴安裝、Slurm 語法檢查及兩端測試

<br>

## 限制與後續方向🚀

- Teacher 與 Student 仍可能產生幻覺或未被影像支持的描述
- Schema 合法只能證明格式正確，不能證明內容具臨床正確性
- 目前評估依賴教師參考與自動指標，仍需病理專家進行外部驗證
- 後續可加入專家盲評、跨資料集泛化測試、校準分析與臨床流程可用性研究
- 系統應持續維持「可見形態描述」與「正式醫療診斷」之間的明確界線

<br>

## 文件導覽📃

- [`NCHC_Intern_Presentation.pptx`](Intern%20Presentation/NCHC_Intern_Presentation.pptx)：實習成果簡報
- [`client_endpoint/README.md`](client_endpoint/README.md)：Windows 使用者端安裝與操作
- [`server_endpoint/README.md`](server_endpoint/README.md)：NCHC Server 端獨立部署
- [`server_endpoint/Student_model/README.md`](server_endpoint/Student_model/README.md)：學生模型下載、放置與驗證
- [`server_endpoint/Localization_model/README.md`](server_endpoint/Localization_model/README.md)：YOLO 權重包與 SHA-256
- [`server_endpoint/TECHNOLOGY_STACK.md`](server_endpoint/TECHNOLOGY_STACK.md)：技術棧
- [`server_endpoint/docs/PROJECT_GUIDE.md`](server_endpoint/docs/PROJECT_GUIDE.md)：維運、交接與資料治理

<br>

## 參考資料📜

1. Zhong, C., et al. [PathVG: A New Benchmark and Dataset for Pathology Visual Grounding](https://arxiv.org/abs/2502.20869). A pathology visual grounding benchmark and the source of the RefPath dataset.
2. Yang, Y., et al. [SkillOpt: Executive Strategy for Self-Evolving Agent Skills](https://arxiv.org/abs/2605.23904). A text-space optimization framework for improving external skills while keeping model weights frozen.
3. Sellergren, A., et al. [MedGemma 1.5 Technical Report](https://arxiv.org/abs/2604.05081). The technical report for the medical multimodal teacher model used in this project.
4. Algomaster, “Client-Server Architecture Explained.”[Online]. Available: https://blog.algomaster.io/p/client-server-architecture-explained
5. IPDEEP, “代理IP新手指南：什麼是SOCKS5代理？”[Online]. Available: https://www.ipdeep.com/zh-Hant/resources/socks5-proxy-guide
6. iThome, “[Day25] Python專案－網頁開發－(4) Fast API 進階後端工程師該思考的幾件事.” [Online]. Available: https://ithelp.ithome.com.tw/articles/10366092
7. Mistral AI, “Model Card for Mistral-Small-3.1-24B-Instruct-2503,”Hugging Face. [Online]. Available: https://huggingface.co/mistralai/Mistral-Small-3.1-24B-Instruct-2503
8. iThome, “Mistral Small 3.1 視覺與語文理解能力領先 Google 新推的 Gemma 3.”[Online]. Available: https://www.ithome.com.tw/news/167926
9. Vocus, “Google MedGemma 1.5：看懂 CT、聽懂醫囑的專業醫療 AI 模型.”[Online]. Available: https://vocus.cc/article/69690df3fd89780001e3e256
10. Google, “Gemma 4 模型總覽,” Google AI for Developers. [Online]. Available: https://ai.google.dev/gemma/docs/core?hl=zh-tw
11. NCHC,　“晶創主機(Nano5) - 使用說明”　 [Online]. Available: https://man.twcc.ai/@AI-Pilot/manual#%E6%99%B6%E5%89%B5%E4%B8%BB%E6%A9%9FNano5---%E4%BD%BF%E7%94%A8%E8%AA%AA%E6%98%8E
