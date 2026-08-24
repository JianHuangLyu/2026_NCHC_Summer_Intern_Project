# Localization models

此目錄保存 PathoVision Server 使用的病理影像候選異常區域定位權重。權重只負責定位，不產生診斷或結構化病理報告。

## 必要檔案

| UI key | 檔名 | 目前大小 | SHA-256 |
|---|---|---:|---|
| yolo11s | yolo11s_best.pt | 19,201,235 bytes | cf4e5586549d2996a1caef20eaef15b4b2c30884c5a8493a465be4f600a6251b |
| yolo11m | yolo11m_best.pt | 40,538,853 bytes | 349190105b061288c600eccc64ecb6276967af0191b7bd21b96147a821341c5b |

## 獨立 YOLO 權重包

YOLO 權重不提交至 GitHub，另行交付：

~~~text
2026_NCHC_Summer_Intern_Project_YOLO_weights.zip
└── Localization_model/
    ├── README.md
    ├── SHA256SUMS
    ├── yolo11s_best.pt
    └── yolo11m_best.pt
~~~

請在 repo 根目錄解壓，檔案會直接落在程式預期的位置：

~~~bash
unzip ../2026_NCHC_Summer_Intern_Project_YOLO_weights.zip -d .
sha256sum -c Localization_model/SHA256SUMS
~~~

若只要驗證權重，也可執行：

~~~bash
python3 scripts/verify_model_assets.py --model all --include-yolo
~~~

上述 Python 指令也會驗證 Student VLM；尚未安裝 Student VLM 時，請先使用 sha256sum 指令單獨驗證 YOLO。

原始訓練產物由專案維護者另行保管；公開程式碼包以本頁記錄的檔名、大小與 SHA-256 作為部署契約。更新權重時必須同步更新本 README、Localization_model/SHA256SUMS、驗證腳本及獨立權重包。
