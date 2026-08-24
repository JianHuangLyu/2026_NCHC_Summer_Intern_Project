# YOLO 異常區域定位模型

此目錄保存 PathoVision Server 使用的病理影像候選異常區域定位權重。YOLO 只負責框選候選區域，不產生診斷或結構化病理報告。

## 必要權重

| 模型 key | 檔名 | 檔案大小 | SHA-256 |
|---|---|---:|---|
| `yolo11s` | `yolo11s_best.pt` | 19,201,235 bytes | `cf4e5586549d2996a1caef20eaef15b4b2c30884c5a8493a465be4f600a6251b` |
| `yolo11m` | `yolo11m_best.pt` | 40,538,853 bytes | `349190105b061288c600eccc64ecb6276967af0191b7bd21b96147a821341c5b` |

YOLO11s 偏向推論速度與資源效率；YOLO11m 在本專案測試中有較佳的 Recall、mAP 與 F1 score。

## 快速安裝

權重包 `2026_NCHC_Summer_Intern_Project_YOLO_weights.zip` 不在公開 repo，請向專案維護者取得。壓縮包 SHA-256：

```text
7731db12b1c3fcdb39fe036772e0b69ab851ce8c80570626da85c5d42737a000
```

將 zip 放在 repo 根目錄後執行：

```bash
cd /work/<USER>/2026_NCHC_Summer_Intern_Project/server_endpoint
sha256sum ../2026_NCHC_Summer_Intern_Project_YOLO_weights.zip
unzip ../2026_NCHC_Summer_Intern_Project_YOLO_weights.zip -d .
sha256sum -c Localization_model/SHA256SUMS
```

完成後兩個 `.pt` 必須位於 `server_endpoint/Localization_model/`。若 Student VLM 也已安裝，可再執行 `python3 scripts/verify_model_assets.py --include-yolo` 驗證全部模型。

## 更新規則

- 原始訓練產物由專案維護者另行保管。
- 替換權重前必須確認訓練來源、模型名稱與人工審查狀態。
- 更新 `.pt` 時要同步更新本 README、`SHA256SUMS`、`scripts/verify_model_assets.py` 與獨立 zip。
- GitHub 交付以檔名、檔案大小與 SHA-256 作為部署契約。
- 權重授權與散布權需由交付者另行確認。
