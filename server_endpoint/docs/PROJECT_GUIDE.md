# 系統整合專案指南

本文件是 `/work/<USER>/2026_NCHC_Summer_Intern_Project/server_endpoint` 的維運與交接入口。此端點負責 NANO4 Slurm 工作、FastAPI、YOLO 定位、分析推論模型服務，以及個案／報告持久化。

訓練與評估 pipeline 由維護者另行管理，不包含在本 GitHub repo。兩者的關係是「訓練與評估產物 → 人工審查 → 系統整合部署」，不應讓系統執行時直接修改訓練輸出。

> 本系統只提供病理影像形態學輔助分析，不可取代病理醫師判讀或正式診斷。

## 文件導覽

| 文件 | 用途 |
|---|---|
| [根目錄 README](../../README.md) | 安裝、啟動、API、操作與故障排除 |
| [技術棧](../TECHNOLOGY_STACK.md) | 技術棧、元件責任與資料流 |
| [Windows Client](../../client_endpoint/README.md) | Windows localhost Client 操作 |
| [YOLO 模型說明](../Localization_model/README.md) | YOLO 權重名稱、來源與驗證 |
| [分析模型說明](../Student_model/README.md) | 分析模型、Prompt／Skill 目錄契約 |

## 目錄責任

| 類型 | 目錄 | 是否屬於原始碼／交付物 | 說明 |
|---|---|---:|---|
| Server 原始碼 | `pathovision_server.py`、`student_vlm.py` | 是 | FastAPI、YOLO、ROI、分析推論、Schema 與個案儲存 |
| Windows Client | `../client_endpoint/` | 是，獨立端點 | Gradio UI、OpenSSH／2FA、SOCKS、Slurm 與 REST Client |
| HPC 啟動 | slurm/ | 是 | 三 GPU 整合式 Stack |
| 測試／工具 | tests/、scripts/ | 是 | 回歸測試與輔助檢查 |
| 定位模型 | Localization_model/ | 目錄與說明是；權重另行管理 | YOLO11s／YOLO11m .pt |
| 分析模型 | Student_model/ | 控制檔是；大型權重另行管理 | 凍結模型快照與各模型的 best_prompt／best_skills |
| 個案資料 | .pathovision_server/ | 否 | 執行時個案、影像、ROI 與 JSON 報告 |
| Slurm runtime | .pathovision_runtime/ | 否 | Job env、狀態與 stdout／stderr |
| Python 環境 | .venv/ | 否 | 可重建，不應打包或提交 |

.pathovision_server/、.pathovision_runtime/、.venv/、模型權重與快取都不應混入純程式碼交付包。若交付包不含權重，仍須保留 Localization_model/README.md、Student_model/README.md 及目錄結構。

## 執行架構

1. Windows 使用者從 Client 登入 NANO4；登入主機與 SSH Port 在 UI 中固定。
2. Client 經互動式 OpenSSH 完成密碼／2FA，提交 slurm/pathovision_vlm_stack.sbatch。
3. Slurm 分配三張 GPU：Gemma、Mistral、FastAPI／YOLO 各一張。
4. Client 經 localhost SOCKS5h Tunnel 呼叫 Compute Node FastAPI。
5. YOLO 保存原圖與候選框；只有使用者勾選的 ROI 才送入分析推論模型。
6. 同一 ROI 可分別由多個模型分析。每份模型 × ROI 報告獨立保存，單一失敗不覆蓋其他報告。
7. 「02 結構化視覺報告」的模型與異常區域是兩個獨立選單；報告頁目前唯讀。
8. 個案與所有 artifacts 只保存在 NANO4 Server。

## Python 環境邊界

不要把 Server、Windows Client 與 vLLM 強行裝在同一個環境。

| 環境 | 依賴來源 | 備註 |
|---|---|---|
| FastAPI／YOLO Server | `requirements.txt` | Server 唯一依賴清單 |
| Windows Client | `../client_endpoint/requirements.txt` | 含 Gradio、Requests/SOCKS、Windows pywinpty；MCP 僅 Python 3.10+ 安裝 |
| vLLM | PATHOVISION_VLLM_BIN 指向的獨立 NANO4 runtime | 不列入 Server requirements，避免 CUDA／PyTorch 依賴衝突 |

## 報告識別與持久化

報告的唯一識別是 (model_key, detection_index)，不是只有 ROI 編號。新格式為：

~~~text
student_vlm_<model-key>_region_<detection-index:03d>.json
~~~

例如兩個模型都分析異常區域 1、2，會保存四份報告：

~~~text
student_vlm_gemma4_region_001.json
student_vlm_gemma4_region_002.json
student_vlm_mistral-small-3.1_region_001.json
student_vlm_mistral-small-3.1_region_002.json
~~~

student_vlm_analysis.json 只供舊版 Client 相容。真實報告集合與狀態以 analysis.json 及各模型 × ROI JSON 為準。

## 訓練產物交接

### YOLO

目前部署權重與訓練專案的最佳權重 SHA-256 完全一致：

| 模型 | 訓練端來源 | 部署端 | SHA-256 |
|---|---|---|---|
| YOLO11s | yolo11s/run_20260726T212850Z_bd7c7b65/weights/best.pt | Localization_model/yolo11s_best.pt | cf4e5586549d2996a1caef20eaef15b4b2c30884c5a8493a465be4f600a6251b |
| YOLO11m | yolo11m/run_20260727T055730Z_90dddbcc/weights/best.pt | Localization_model/yolo11m_best.pt | 349190105b061288c600eccc64ecb6276967af0191b7bd21b96147a821341c5b |

### 分析推論模型

SkillOpt 實驗不更新 Gemma／Mistral 權重。可部署產物是 Prompt／Skill Markdown；模型權重是獨立下載的凍結 Hugging Face 快照。

目前兩個模型的 Cellular_Cytoplasmic_and_Nuclear_Morphology.md 與各自 Skill 分支的 skillopt/best_skill.md 雜湊一致。部署 Prompt 則在最佳候選上加入專業繁體中文輸出規則，所以不應直接以原始 best_skill.md 覆蓋：

| 模型 | 產物 | 訓練候選 SHA-256 | 目前部署 SHA-256 | 關係 |
|---|---|---|---|---|
| Gemma4 | Prompt | 65650a96...c84e | cec922a5...c77 | 部署版含 zh-Hant-TW 規則 |
| Gemma4 | Cellular Skill | 0dcc7fc9...4500 | 0dcc7fc9...4500 | 完全一致 |
| Mistral | Prompt | d7e55a1a...4fe | 13187bad...059 | 部署版含 zh-Hant-TW 規則 |
| Mistral | Cellular Skill | c52fcb2c...10db | c52fcb2c...10db | 完全一致 |

更新控制層時應逐檔審查，不要整個覆蓋 best_prompt/ 或 best_skills/。Output_Schema.json、Skill_Registry.yaml、欄位映射與其他 Skills 都是推論契約的一部分。

## 安全更新流程

1. 在訓練端確認 completion_check_prompt.json／completion_check_skill.json 為 complete。
2. 以 pipeline_summary.json、baseline／final eval_summary.json 與專業人工複核判斷候選，不只看訓練 log。
3. 將候選放到暫存目錄，先做 diff -u 與 sha256sum。
4. 若是 Prompt，合併並保留部署端繁體中文、Schema、非診斷性與可見證據限制。
5. 一次只更新指定模型的一個元件，保留更新前版本與雜湊。
6. 執行完整測試與模型資產檢查後才啟動正式 Slurm 工作。
7. 大型權重、.env、token、個案與 runtime 不進入程式碼交付包。

## 驗證指令

~~~bash
cd /work/<USER>/2026_NCHC_Summer_Intern_Project/server_endpoint

# Server／Schema／ROI／持久化
.venv/bin/python -m unittest discover -v tests

# Windows Client 邏輯（Windows venv 中執行較完整）
cd ../client_endpoint
python -m unittest discover -v . "test_*.py"

# Slurm 語法
cd ../server_endpoint
bash -n slurm/pathovision_vlm_stack.sbatch

# 部署權重／控制檔雜湊
sha256sum Localization_model/*.pt
sha256sum Student_model/*/best_prompt/Prompt.md
~~~

## 機密與資料治理

- 不提交 .env、API key、Hugging Face token、SSH 密碼或 OTP。
- 密碼與 2FA 只送入互動式 SSH 終端，不放在命令列。
- .pathovision_server/ 可能含醫療影像與個案資訊；備份、搬移與刪除前須依資料治理規範處理。
- 不要以壓縮檔或版本控制任意散布模型權重；先確認模型授權與存取權。
