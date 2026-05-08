# Nail Mask Tagging Tool

Web-based 人工精修工具，用於載入 model 輸出的 nail mask 並進行 per-finger 校正。

---

## 環境設定

```bash
conda create -n nail-tagger python=3.9 flask -y
conda activate nail-tagger
```

---

## 啟動

```bash
bash run.sh
```

開啟瀏覽器 → `http://localhost:5000`

### 參數說明

| 參數 | 說明 |
|---|---|
| `-c` / `--control` | 原始手部圖片資料夾（左側參考用） |
| `-t` / `--target`  | 美甲手部圖片資料夾（右側編輯用） |
| `-m` / `--mask`    | model 輸出的 `src_sized_mask` 資料夾（初始 mask 來源） |

---

## 前置作業：產生 model mask

在執行 tagging tool 之前，需先跑 inference 產出 `src_sized_mask`
https://github.com/perfect-corp/DeepTrainer_Ring/blob/master/NailSegment/test_point.py

---

## 介面說明

```
┌─ Navbar ──────────────────────────────────────────────────┐
│  ← Prev  Next →  [image name]  [Save Finger]  [Save All]  │
├─ 左 Panel ────────────┬─ 右 Panel ────────────────────────┤
│  Control（參考圖）    │  Target + Mask（可編輯）           │
├─ Toolbar ─────────────┴───────────────────────────────────┤
│  [拇指][食指][中指][無名指][小指]                          │
│  [✏ Paint][◯ Erase][✕ Clear]  Brush ──  Opacity ──        │
└───────────────────────────────────────────────────────────┘
```

---

## 操作快捷鍵

### 導航

| 操作 | 效果 |
|---|---|
| `←` / `→` | 切換上 / 下一張圖片 |
| `1` – `5` | 選擇手指（拇指 → 小指） |

### 編輯

| 操作 | 效果 |
|---|---|
| `P` | 切換為 Paint（塗抹）模式 |
| `E` | 切換為 Erase（橡皮擦）模式 |
| `Ctrl+Z` / `Cmd+Z` | 復原（最多 20 步） |

### 視圖

| 操作 | 效果 |
|---|---|
| `Ctrl+drag` / `Cmd+drag` | 平移畫面 |
| `Ctrl+scroll` / `Cmd+scroll` | 縮放（以滑鼠位置為中心） |
| `Ctrl+0` / `Cmd+0` | 重置縮放至 fit to screen |

---

## Finger Tab 狀態標記

| 標記 | 意義 |
|---|---|
| `✓` | 已人工儲存過 |
| `·` | 僅有 model 輸出結果 |
| （空白） | 無任何 mask |

---

## 輸出

存檔位置：`tagging_tool/tagged_masks/`

格式：grayscale PNG，白色 = nail，黑色 = 背景

命名規則：`{image_stem}_mask_{0~4}.png`

人工儲存的 mask 優先於 model 輸出；下次開啟同一張圖會自動載入已儲存版本。
