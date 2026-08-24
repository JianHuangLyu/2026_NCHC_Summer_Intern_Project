# PathoVision Windows 使用者端

`client_endpoint/` 是可獨立放到 Windows localhost 的 Gradio 使用者端。它負責登入 NCHC NANO4、提交與管理 Slurm 工作、建立 SOCKS5h 通道、呼叫 REST API，以及呈現定位與結構化報告。

模型權重、GPU 推論、病理影像與個案 artifacts 全部保留在 NANO4 的 `server_endpoint/`，不會複製到本機。

> 本工具僅供研究與形態學輔助分析，不可取代病理醫師判讀或正式診斷。

## 功能

- 使用 Windows 原生 `ssh.exe` 進行密碼與二階段驗證。
- 維持 OpenSSH 工作階段並建立 localhost SOCKS5h 代理。
- 自動尋找 NANO4 上的 `server_endpoint/`。
- 產生並提交三 GPU Slurm 工作。
- 顯示 REST、Gemma4 與 Mistral Small 3.1 的即時載入狀態。
- 上傳原始影像並選擇 YOLO11s 或 YOLO11m。
- 勾選一個或多個 ROI，只分析被選取的區域。
- 以獨立下拉選單切換模型與 ROI 報告。
- 管理個案資料，並在結束工作階段時歸還 Slurm 資源。

## 系統需求

- Windows 10 或 Windows 11。
- Python 3.9 以上。
- Windows OpenSSH Client。
- 可連線至 NANO4 登入節點的網路環境。
- 有效的 NANO4 帳號、密碼與二階段驗證方式。

## 安裝

在 Windows PowerShell 執行：

```powershell
cd client_endpoint
py -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

不需要選配 MCP façade 時可執行：

```powershell
.venv\Scripts\python app.py --no-mcp
```

預設介面位址為 `http://127.0.0.1:8200`。

## NANO4 Server 必要結構

使用者端會尋找以下標記檔：

```text
/work/<USER>/2026_NCHC_Summer_Intern_Project/server_endpoint/pathovision_server.py
```

`server_endpoint/` 至少必須包含：

```text
server_endpoint/
├── pathovision_server.py
├── student_vlm.py
├── slurm/
├── Localization_model/
└── Student_model/
```

如果使用非標準位置，可在遠端環境設定 `PATHOVISION_PROJECT_DIR`，其值必須直接指向 `server_endpoint/`，而不是 repo 根目錄。

## 操作流程

1. 開啟 Client，輸入 NANO4 帳號、密碼並選擇二階段驗證方式。
2. 完成 OTP 或推播驗證。
3. 選擇偵測到的 `server_endpoint/` 路徑。
4. 選擇 Slurm partition、account、執行時間與資源。
5. 提交工作；Job 進入 `RUNNING` 後會直接進入分析頁。
6. 等待頁面顯示 REST 與所需學生模型已就緒。
7. 上傳影像並先執行 YOLO 定位。
8. 勾選 ROI，再選擇學生模型進行結構化分析。
9. 在報告頁切換模型與 ROI，或在個案紀錄頁管理資料。
10. 使用「結束工作階段並歸還資源」或正常關閉 Client。

## 三 GPU 配置

Client 會提交 `server_endpoint/slurm/pathovision_vlm_stack.sbatch`，預設使用：

| GPU | 服務 |
|---:|---|
| 0 | Gemma4 31B／vLLM |
| 1 | Mistral Small 3.1 24B／vLLM |
| 2 | FastAPI／YOLO11s／YOLO11m |

Slurm allocation 一進入 `RUNNING`，分析頁即可開啟。Gemma 與 Mistral 會平行載入；Client 每兩秒輪詢狀態，單一模型完成後便加入模型選單，不必等待另一個模型。

## 安全設計

- 密碼與 OTP 只寫入互動式 OpenSSH pseudoterminal，不加入命令列。
- `StrictHostKeyChecking=accept-new` 只接受首次出現的主機金鑰；既有金鑰變更時會停止連線。
- REST 請求使用該次工作隨機產生的 `X-API-Key`。
- SOCKS5h 由遠端解析計算節點名稱，不依賴 Windows DNS。
- Client 只取消自己提交的 Slurm Job。
- 模型與影像不會下載到 Windows。

## 測試

```powershell
cd client_endpoint
python -m unittest discover -v . "test_*.py"
```

## 常見問題

- **找不到 Server 專案**：確認 NANO4 上存在 `server_endpoint/pathovision_server.py`，或設定 `PATHOVISION_PROJECT_DIR`。
- **找不到 `ssh.exe`**：到 Windows「選用功能」安裝 OpenSSH Client。
- **登入逾時**：確認密碼、二階段驗證選項、OTP 與手機推播。
- **已進入分析頁但模型不可選**：查看載入進度；大型模型首次啟動需要較長時間。
- **沒有 ROI 可分析**：YOLO 沒有偵測結果時，系統依設計不呼叫學生模型。
- **關閉後 Job 仍存在**：重新登入後以 `squeue` 確認，必要時只取消自己的 Job。
