# VinCert

**Extract certificate entries with OCR and auto-fill them into a website form.**

VinCert is a desktop tool for batch-importing certificates, reviewing OCR results, and filling verified fields into an open browser tab.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![customtkinter](https://img.shields.io/badge/UI-customtkinter-lightblue)](https://github.com/TomSchimansky/CustomTkinter)
[![Status](https://img.shields.io/badge/status-prototype-yellow)](.)
[![Target](https://img.shields.io/badge/target-Windows-0078D4)](.)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python gui.py
```

---

# v0.1 Release Notes

**Release date:** 20 July 2026

First working prototype of VinCert — a desktop app for batch-extracting metrology certificate fields from PDFs and preparing them for manual review before web-form autofill.

## Highlights

- **Two-step workflow:** 批量提取 → 核对填写
- **Folder batch import** — pick a folder; root-level PDFs are scanned and parsed automatically
- **Multi-page PDF preview** — all pages rendered in a scrollable canvas, scaled to window width
- **Rule-based field extraction** from embedded PDF text (no OCR yet) for 天溯-style calibration certificates
- **Approve queue** — reviewed certificates accumulate for a future batch autofill step

## Workflow

### 1. 批量提取

1. Click **选择文件夹…** and pick a directory containing certificate PDFs (root level only).
2. Select a document from the list; the center panel shows a scrollable preview of every page.
3. Parsed fields appear in the right sidebar, grouped as:
   - **比对字段** — used later to match the correct record on the target website
   - **填写字段** — values intended for form autofill
4. Expand **提取文本（封面页）** to inspect raw cover-page text.
5. Click **前往核对填写 →** when ready.

### 2. 核对填写

1. Step through certificates with prev/next navigation.
2. Edit the four fill fields if needed.
3. **批准** adds the certificate to the autofill queue; **跳过** moves on; **移出队列** removes an approved entry.
4. **自动填写 (N)** is wired in the UI but does not yet write to a webpage.

## Extracted fields

| Group | Field | Notes |
|-------|-------|-------|
| 比对字段 | 计量器具名称 | Match key for webpage verification |
| 比对字段 | 计量器具编号 | From 出厂编号 / 计量器具编号 |
| 比对字段 | 制造厂 | Match key for webpage verification |
| 填写字段 | 检验方式 | 检定 / 校准 / 校验 |
| 填写字段 | 本次检测日期 | `YYYY-MM-DD` |
| 填写字段 | 本次检测有效期至 | `YYYY-MM-DD` |
| 填写字段 | 检测机构 | Issuer / testing organization |

Additional metadata (证书编号, 客户名称, 型号/规格, etc.) is parsed internally but not shown in the main UI.

## Technical notes

- **Extraction method:** PyMuPDF embedded text from cover page (page 0)
- **Parser:** Label-driven rules in `vincert/parse_metrology.py`; normalizes spaced CJK labels (e.g. `校 准 证 书` → `校准证书`)
- **UI:** CustomTkinter desktop app (`gui.py`)
- **Dependencies:** `customtkinter`, `pymupdf`, `Pillow`
- **Tested on:** macOS during development; Windows is the deployment target

## Known limitations

- **Digital PDFs only** — scanned/image PDFs have no OCR fallback yet
- **Issuer-specific parser** — tuned for 深圳天溯 calibration certificate layout; other formats may parse poorly
- **Root-level PDFs only** — subfolders are not scanned
- **Browser embed & autofill not implemented** — the website panel and **自动填写** button are placeholders
- **Settings** — sidebar settings button is a stub

## What's next (post-v0.1)

- Browser embedding and EAMS/web form autofill
- Webpage element matching using 比对字段 before filling
- PaddleOCR fallback for scanned certificates
- Additional issuer parsers beyond 天溯 format