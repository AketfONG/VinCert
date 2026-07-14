# VinCert

**Extract certificate entries with OCR and auto-fill them into a website form.**

VinCert is a desktop tool for batch-importing certificates, reviewing OCR results, and filling verified fields into an open browser tab.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![customtkinter](https://img.shields.io/badge/UI-customtkinter-lightgrey)](https://github.com/TomSchimansky/CustomTkinter)
[![Status](https://img.shields.io/badge/status-prototype-yellow)](.)
[![Target](https://img.shields.io/badge/target-Windows-0078D4)](.)

## Features (v0: Initial Prototype)

On open, VinCert feels like a dedicated workstation: a fixed left sidebar for navigation, a wide center panel reserved for the target website, and a right control rail for the current step. A progress bar and step tiles (批量导入 → 人工审核 → 自动填写) show where you are in the flow, while still letting you jump between stages freely.

In batch import, you pick certificate images or PDFs, see them listed in the side panel, and start the import pipeline when ready. In manual check, you move through certs one by one (`1/10` style), inspect a preview area, correct fields such as name and ID, then 批准 or 跳过. Auto-fill keeps the same cert navigator and puts a single 填写 action next to the website you are targeting via the URL bar (open / refresh placeholders for the future embedded browser).

The whole shell is in Simplified Chinese and follows system light or dark appearance.
