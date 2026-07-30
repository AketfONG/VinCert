# VinCert

**Extract certificate entries with OCR and auto-fill them into a website form.**

VinCert is a desktop tool for batch-importing certificates, reviewing OCR results, and filling verified fields into an open browser tab.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![customtkinter](https://img.shields.io/badge/UI-customtkinter-lightblue)](https://github.com/TomSchimansky/CustomTkinter)
[![Status](https://img.shields.io/badge/status-prototype-yellow)](.)
[![Target](https://img.shields.io/badge/target-Windows-0078D4)](.)

## Setup (Windows)

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install paddlepaddle==2.6.2 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
pip install -r requirements.txt
python gui.py
```

Use official **64-bit Python 3.11** and enable **tcl/tk**. Digital PDFs parse from embedded text; scanned PDFs fall back to PaddleOCR (same approach as 证书扫描, without Django).

In `核对填写`, approved entries can also be exported to Excel for later batch import. The generated workbook uses this column order: `名称`, `编号`, `型号`, `计量单位`, `计量日期`, `计量类型`.

---
# v0.2 (29/07/2026) Release Notes

- **Simplification** — removed custom PDF viewer & placeholder browser view for seperated control and better functionality
- **Updated UI & UX** — Cleaner document control & operations
- **Installation Ease** — requirements.txt to ensure consistent environments
