# VinCert：EAMS 台账更新自动化工具 用户文档

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![customtkinter](https://img.shields.io/badge/UI-customtkinter-lightblue)](https://github.com/TomSchimansky/CustomTkinter)
[![Status](https://img.shields.io/badge/status-prototype-yellow)](.)
[![Target](https://img.shields.io/badge/target-Windows-0078D4)](.)

## 一、安装与启动

1. 安装 VSCode，把软件文件夹（e.g. VinCert v1.0）拖到 VSCode 里，并信任文件夹，安装 VSCode 里面的 Python Extension 附加功能，以可以打开代码并看到 gui.py 上的运行按钮为成功
2. 安装 python: 用 python 3.11.9 安装器安装 python 3.11.9, 以输入指令 `py --version` 返回 3.11.x 为成功
3. 配置环境: 在软件文件夹所在终端运行以下命令：

```bash
py -m pip install -r requirements.txt
```
4. 创建快捷方式: 创造 txt 文档，输入:

```bash
@echo off
py D:/... (gui.py 路径)
pause
```
将 txt 格式改成 bat 后缀，双击运行

如果显示 pip 版本太旧提示更新，按终端提示操作更新 pip

成功开启图形界面后，运行 `playwright install` 方可保证浏览器窗口精致功能正常

## 二、首次设置

打开左下角 设置：

1. EAMS 登录：填写用户名、密码，点击 保存登录信息， 实现自动登录系统功能
2. 步骤间隔：每个自动化步骤之间的等待秒数（默认 1 秒），运行一段时间后没问题可以改成 0 秒以达到更快速度
3. 启动加载文件夹：可配置证书文件夹，启动时自动加载
4. 其他可选：界面放大、更小窗口、失败证书目录等
5. 解析规则：为新证书添加新规则
6. 功能拓展：指定自定义批量导入 EAMS 的 Excel 模版，并完成自动化操作

## 三、使用流程

### 第一步：提取核对

1. 导入文件夹（证书放在文件夹 root）
2. 移除失败证书（无有效文本信息）
3. 同屏检查提取信息并批准（加入队列）/移除（踢出队列）

### 第二步：自动化

1. 导出 excel：移入导入文件夹里面新建的 exports 文件夹
2. 自动化途中导入失败：移入导入文件夹里面新建的 failed_items 文件夹
3. 中途可以暂停/继续自动化操作，将在先前停止的步骤继续流程

### 使用拓展功能（紫色模式）：

1. 导入文件夹（证书放在文件夹 root）
2. 到自动化指定使用的 excel 批量导入 EAMS 模版，软件会根据提取出来的器具编号匹配 pdf，完成自动化

### 点击 导出并自动填写 后大致流程：

1. 登录 EAMS（正式或测试环境，取决于设定里面测试模式的设置）
2. 若出现门户页上的 Available Manage，自动点击进入
3. 进入 计量器具结果录入
4. 批量导入 Excel，等待「导入成功」
5. 按批准列表：按编号定位 → 核对字段 → 填写检验方式/日期等 → 上传对应 PDF（类型选「证书」）

## 四：注意事项

1. 该软件不具备 OCR 功能
2. VinCert 文件夹里的 exports 和 failed_items 文件夹已经不具备任何作用
3. 如果安装失败，多数情况是python路径问题

## 五：后续开发

1. `playwright codegen https://url(网址)` + inspect 网站 可以开启监听窗口提取操作流程代码和判断元素状态（输入框里有什么事信息，程序可以根据状态调整行为）
2. 后续使用其他软件更改 python 版本可能导致软件出错，届时可能需要用到 venv 虚拟环境配置

---
 
# v1.0 (18/08/2026) Release Notes

- **Purple Mode** — Allow choosing custom excel spreadsheet for batch import and automation, software would choose appropriate pdfs for certificate uploads
- **Bug Fixes & Improvements** — Windows Zoom Fix + Smaller Window Option, Updated UX, Fixed Incorrect Parsing
