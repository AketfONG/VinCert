# VinCert

**Extract certificate entries with OCR and auto-fill them into a website form.**

VinCert is a desktop tool for batch-importing certificates, reviewing OCR results, and filling verified fields into an open browser tab.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![customtkinter](https://img.shields.io/badge/UI-customtkinter-lightblue)](https://github.com/TomSchimansky/CustomTkinter)
[![Status](https://img.shields.io/badge/status-prototype-yellow)](.)
[![Target](https://img.shields.io/badge/target-Windows-0078D4)](.)

---
# v0.1 (21/07/2026) Release Notes

- **New Two-step workflow:** 批量提取 → 核对填写
- **Folder batch import** — pick a folder; root-level PDFs are scanned and parsed automatically
- **Multi-page PDF preview** — all pages rendered in a scrollable canvas, scaled to window width
- **Rule-based field extraction** from embedded PDF text (no OCR yet) specialised for 天溯 calibration certificates
- **Approve queue** — reviewed certificates accumulate for a future batch autofill step
