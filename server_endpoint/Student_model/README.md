# 學生多模態模型安裝與配置

此目錄保存 PathoVision 的凍結學生多模態模型，以及各模型專用的 Prompt、Skills 與 JSON Schema。SkillOpt 只最佳化外部文字控制層，不會產生或修改 VLM 權重。

模型只需要放在 NCHC 的 `server_endpoint/Student_model/`，不應複製到 Windows `client_endpoint/`。

## 模型與固定放置位置

| 模型 key | Hugging Face 模型 | 固定目錄 | 約需空間 |
|---|---|---|---:|
| `gemma4` | [google/gemma-4-31B-it](https://huggingface.co/google/gemma-4-31B-it) | `Student_model/Gemma4/gemma-4-31B-it/` | 63 GB |
| `mistral-small-3.1` | [mistralai/Mistral-Small-3.1-24B-Instruct-2503](https://huggingface.co/mistralai/Mistral-Small-3.1-24B-Instruct-2503) | `Student_model/Mistral-Small-3.1/mistral-small-3.1-24b-instruct-2503/` | 97 GB |

兩個模型合計約 160 GB；建議至少預留 180 GB。上游 `main` 分支大小可能改變。若模型頁要求接受使用條款，請先登入 Hugging Face 完成授權。

## 自動安裝

需求：Linux、Bash、Python 3.9 以上，以及可連線 Hugging Face 的節點。

腳本會執行以下工作：

1. 在 `server_endpoint/.hf-model-installer/` 建立獨立環境。
2. 安裝或更新 `huggingface_hub`。
3. 將模型快照下載到程式固定使用的目錄。
4. 支援 Hugging Face 的續傳機制；中斷後可直接重跑。
5. 驗證模型設定、權重分片、Prompt、Schema 與 Skills。

```bash
cd /work/<USER>/2026_NCHC_Summer_Intern_Project/server_endpoint

# 模型需要授權時先登入；憑證不會寫入 repo
python3 -m pip install --user --upgrade huggingface_hub
hf auth login

# 下載兩個模型
./scripts/install_student_vlm.sh

# 或只下載指定模型
./scripts/install_student_vlm.sh --model gemma4
./scripts/install_student_vlm.sh --model mistral-small-3.1
```

也可使用環境變數登入：

```bash
export HF_TOKEN=<你的存取權杖>
./scripts/install_student_vlm.sh
```

不要將 token 寫入 `.env.example`、腳本、README、Shell 歷史範例或 Git commit。

## 固定上游版本

預設下載各模型的 `main`。正式部署若要求可重現，建議指定經審查的 commit revision：

```bash
export PATHOVISION_GEMMA_REVISION=<gemma-commit>
export PATHOVISION_MISTRAL_REVISION=<mistral-commit>
./scripts/install_student_vlm.sh
```

## 手動下載

使用 Hugging Face 官方 `hf` CLI 時，`--local-dir` 必須完全符合下列路徑：

```bash
cd /work/<USER>/2026_NCHC_Summer_Intern_Project/server_endpoint

hf download google/gemma-4-31B-it \
  --local-dir Student_model/Gemma4/gemma-4-31B-it

hf download mistralai/Mistral-Small-3.1-24B-Instruct-2503 \
  --local-dir Student_model/Mistral-Small-3.1/mistral-small-3.1-24b-instruct-2503
```

下載後執行驗證：

```bash
./scripts/install_student_vlm.sh --verify-only
```

## 僅驗證既有模型

```bash
# 驗證兩個模型、分片索引、Prompt、Schema 與 Skills
./scripts/install_student_vlm.sh --verify-only

# 只驗證單一模型
./scripts/install_student_vlm.sh --verify-only --model gemma4
./scripts/install_student_vlm.sh --verify-only --model mistral-small-3.1

# 同時驗證 YOLO 權重
python3 scripts/verify_model_assets.py --include-yolo
```

Slurm 正式工作預設設定 `HF_HUB_OFFLINE=1` 與 `TRANSFORMERS_OFFLINE=1`，因此必須在提交工作前完成下載及驗證。

## 正式目錄契約

```text
Student_model/
├── Gemma4/
│   ├── gemma-4-31B-it/                   # 凍結模型快照，下載產生
│   ├── best_prompt/                      # Prompt、Schema、Registry、欄位映射
│   └── best_skills/                      # Registry 所需 Markdown Skills
└── Mistral-Small-3.1/
    ├── mistral-small-3.1-24b-instruct-2503/
    ├── best_prompt/
    └── best_skills/
```

每個模型至少需要：

```text
<模型權重目錄>/config.json
<模型權重目錄>/*.safetensors
best_prompt/Prompt.md
best_prompt/Global_Rules.md
best_prompt/Output_Schema.json
best_prompt/Output_Field_Skill_Mapping.yaml
best_prompt/Skill_Registry.yaml
best_skills/<Registry 指定的 Skill>.md
```

FastAPI 只有在權重、控制檔與對應 vLLM `/v1/models` endpoint 都就緒時，才會回報 `inference_ready=true`。

## Prompt 與 Skills 更新原則

- 目前 GitHub repo 不包含 SkillOpt 訓練 pipeline，只保存人工審查後的部署產物。
- 不可讓線上推論服務直接修改 `best_prompt/` 或 `best_skills/`。
- 更新前須比較差異、記錄 SHA-256，並保留非診斷性、可見證據與繁體中文輸出規則。
- `Output_Schema.json`、`Skill_Registry.yaml`、欄位映射與 Skills 是同一份推論契約，不可只更新其中一個檔案。
- 更新後必須執行資產驗證與完整測試。

## vLLM 執行環境

vLLM 應使用獨立 Python 環境，避免與 FastAPI／YOLO 的 CUDA、PyTorch 版本衝突：

```bash
export PATHOVISION_VLLM_BIN=/absolute/path/to/vllm
```

三 GPU Slurm Stack 會用 GPU 0 啟動 Gemma4、GPU 1 啟動 Mistral，並將兩個 endpoint 綁定於計算節點 `127.0.0.1`。
