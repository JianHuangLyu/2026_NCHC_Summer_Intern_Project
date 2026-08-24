# 學生多模態模型安裝與配置

此目錄保存 PathoVision 的凍結學生多模態模型，以及各模型專用的 Prompt、Skills 與 JSON Schema。SkillOpt 只最佳化外部文字控制層，不會產生或修改 VLM 權重。

模型只需要放在 NCHC 的 `server_endpoint/Student_model/`，不應複製到 Windows `client_endpoint/`。

## 模型與固定放置位置

| 模型 key | Hugging Face 模型 | 固定目錄 | 約需空間 |
|---|---|---|---:|
| `gemma4` | [google/gemma-4-31B-it](https://huggingface.co/google/gemma-4-31B-it) | `Student_model/Gemma4/gemma-4-31B-it/` | 63 GB |
| `mistral-small-3.1` | [mistralai/Mistral-Small-3.1-24B-Instruct-2503](https://huggingface.co/mistralai/Mistral-Small-3.1-24B-Instruct-2503) | `Student_model/Mistral-Small-3.1/mistral-small-3.1-24b-instruct-2503/` | 97 GB |

兩個模型合計約 160 GB；建議至少預留 180 GB。上游 `main` 分支大小可能改變。若模型頁要求接受使用條款，請先登入 Hugging Face 完成授權。

## 快速安裝

需求：Linux、Bash、Python 3.9 以上，且可連線 Hugging Face。

```bash
cd /work/<USER>/2026_NCHC_Summer_Intern_Project/server_endpoint
python3 -m pip install --user --upgrade huggingface_hub
hf auth login
./scripts/install_student_vlm.sh
```

腳本會下載兩個模型到固定位置並自動驗證；下載中斷時直接重跑。

常用選項：

```bash
# 只安裝一個模型
./scripts/install_student_vlm.sh --model gemma4
./scripts/install_student_vlm.sh --model mistral-small-3.1

# 只驗證，不下載
./scripts/install_student_vlm.sh --verify-only
```

需要固定版本時，在執行前設定 `PATHOVISION_GEMMA_REVISION` 與 `PATHOVISION_MISTRAL_REVISION`；也可用 `HF_TOKEN` 取代互動式登入。Slurm 預設離線載入，所以提交 Job 前必須完成下載與驗證。

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
