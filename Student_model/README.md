# Analysis inference models

此目錄保存 PathoVision 的凍結分析推論模型，以及每個模型專用的 Prompt／Skill 控制層。SkillOpt 最佳化不會產生或修改 VLM 權重。

## Student VLM 一鍵安裝

GitHub repo 不包含大型模型快照，只保留 PathoVision 專用的 Prompt、JSON Schema 與 Skills。請在 NCHC Server 的 repo 根目錄執行下載；不要把 Student_model 複製到 Windows Client。

| 模型 key | Hugging Face repo | 必須放置的位置 | 目前約需空間 |
|---|---|---|---:|
| gemma4 | [google/gemma-4-31B-it](https://huggingface.co/google/gemma-4-31B-it) | Student_model/Gemma4/gemma-4-31B-it/ | 63 GB |
| mistral-small-3.1 | [mistralai/Mistral-Small-3.1-24B-Instruct-2503](https://huggingface.co/mistralai/Mistral-Small-3.1-24B-Instruct-2503) | Student_model/Mistral-Small-3.1/mistral-small-3.1-24b-instruct-2503/ | 97 GB |

兩個模型合計約 160 GB；建議至少預留 180 GB。上游 main 分支大小可能改變。若模型頁要求同意條款或登入，請先在 Hugging Face 完成授權。

### 自動下載（建議）

需求：Linux、Bash、Python 3.9 以上、可連線 Hugging Face 的節點。腳本會在 .hf-model-installer/ 建立獨立環境、安裝 huggingface_hub、續傳模型快照，最後驗證權重與控制檔。此環境和模型目錄均已列入 .gitignore。

~~~bash
cd /work/<USER>/2026_NCHC_Summer_Intern_Project

# 若模型需要登入，先執行一次；憑證不會寫入 repo。
python3 -m pip install --user --upgrade huggingface_hub
hf auth login

# 下載兩個模型
./scripts/install_student_vlm.sh

# 或只下載其中一個
./scripts/install_student_vlm.sh --model gemma4
./scripts/install_student_vlm.sh --model mistral-small-3.1
~~~

下載中斷時直接重跑相同指令即可。若要固定可重現的上游版本，先指定 commit revision：

~~~bash
export PATHOVISION_GEMMA_REVISION=<gemma-commit>
export PATHOVISION_MISTRAL_REVISION=<mistral-commit>
./scripts/install_student_vlm.sh
~~~

也可透過 HF_TOKEN 環境變數登入；不要將 token 寫入 .env.example、腳本或 Git commit。

### 手動下載

使用 Hugging Face 官方 hf CLI 時，local-dir 必須與下列路徑完全一致：

~~~bash
hf download google/gemma-4-31B-it \
  --local-dir Student_model/Gemma4/gemma-4-31B-it

hf download mistralai/Mistral-Small-3.1-24B-Instruct-2503 \
  --local-dir Student_model/Mistral-Small-3.1/mistral-small-3.1-24b-instruct-2503
~~~

### 驗證與離線執行

~~~bash
# 不下載，只驗證兩個模型、分片索引、Prompt 與 Skills
./scripts/install_student_vlm.sh --verify-only

# 只驗證單一模型
./scripts/install_student_vlm.sh --verify-only --model gemma4
~~~

Slurm 工作預設以離線模式載入，因此必須先在可連網節點完成下載與驗證。vLLM 請使用獨立環境，並以 PATHOVISION_VLLM_BIN 指向其執行檔；大型模型權重不應提交至 GitHub 或一般 Git LFS。

## 正式部署目錄契約

~~~text
Student_model/
├── Gemma4/
│   ├── gemma-4-31B-it/                   # 凍結模型快照，另行取得
│   ├── best_prompt/                      # Prompt、Schema、Registry、欄位映射
│   └── best_skills/                      # Registry 所需 Markdown Skills
└── Mistral-Small-3.1/
    ├── mistral-small-3.1-24b-instruct-2503/
    ├── best_prompt/
    └── best_skills/
~~~

每個可用模型至少需要：

~~~text
<model-weight-dir>/config.json
<model-weight-dir>/*.safetensors
best_prompt/Prompt.md
best_prompt/Global_Rules.md
best_prompt/Output_Schema.json
best_prompt/Output_Field_Skill_Mapping.yaml
best_prompt/Skill_Registry.yaml
best_skills/<registry-required-skill>.md
~~~

FastAPI 只有在上述資產與相應 vLLM /v1/models endpoint 都就緒時，才回報 inference_ready=true。

## 與訓練產物的關係

本 GitHub repo 不包含訓練 pipeline。經人工審查後可交付的 SkillOpt 產物依元件代表：

- component=prompt：Prompt 候選；
- component=skill：Cellular_Cytoplasmic_and_Nuclear_Morphology.md 候選。

目前部署 Prompt 額外加入專業繁體中文輸出規則，不能不經 diff／人工複核便直接覆蓋。更新原則與雜湊交接方式請見 [系統整合專案指南](../docs/PROJECT_GUIDE.md)。

大型權重不應放入純程式碼壓縮包或公開版本控制。省略權重時仍須保留各模型目錄、best_prompt/、best_skills/ 與本 README。
