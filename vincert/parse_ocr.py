"""OCR-line field extraction adapted from ji_liang ``text_to_info_tools``.

These heuristics target noisy cover-page OCR line lists (not clean digital PDF text).
"""

from __future__ import annotations

import datetime
import re
from difflib import SequenceMatcher
from datetime import timedelta

from .models import CertificateFields

# Known issuers — fuzzy-matched against the joined OCR blob.
CALIBRATION_UNITS = [
    "中国计量科学研究院",
    "北京市计量检测科学研究院",
    "北京航天计量测试技术研究所",
    "北京长城计量测试技术研究所",
    "广州力赛计量检测有限公司",
    "广电计量检测集团股份有限公司",
    "民航计量检测中心",
    "泰尔实验室",
    "阿米检测技术有限公司",
    "深圳天溯计量检测股份有限公司",
    "上海市计量测试技术研究院",
    "北京计量检测研究院",
    "华测检测有限公司",
    "成都市计量检定测试院",
    "广西桂景计量检测有限公司",
    "东莞市帝恩检测有限公司",
    "北京能克方圆方圆科技有限公司",
    "上海中田计量校准检测技术服务有限公司",
    "中国铁路北京局集团有限公司计量管理所",
]

_FAIL = "无法识别"


def _remove_punctuation(text: str) -> str:
    text = re.sub(r"[^\u4e00-\u9fa5\w\s]", "", text)
    return text.replace(" ", "")


def _strip_fail(value: str) -> str:
    value = (value or "").strip()
    if not value or value.startswith(_FAIL):
        return ""
    return value


def _partial_ratio(a: str, b: str) -> float:
    """Approximate fuzzywuzzy.partial_ratio without the dependency."""
    if not a or not b:
        return 0.0
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if short in long:
        return 100.0
    best = 0.0
    n = len(short)
    for i in range(0, len(long) - n + 1):
        score = SequenceMatcher(None, short, long[i : i + n]).ratio() * 100.0
        if score > best:
            best = score
    return best


def recognize_calibration_unit(dstr: str) -> str:
    if not dstr:
        return ""
    for unit in CALIBRATION_UNITS:
        if unit in dstr:
            return unit
    scores = [_partial_ratio(cu, dstr) for cu in CALIBRATION_UNITS]
    max_value = max(scores) if scores else 0.0
    if max_value > 80:
        cu = CALIBRATION_UNITS[scores.index(max_value)]
        if cu == "泰雷實验室":
            cu = "泰尔实验室"
        return _remove_punctuation(cu)
    return ""


def recognize_calibration_type(dstr: str) -> str:
    for label, code in (("检定证书", "检定"), ("校准证书", "校准"), ("校验证书", "校验")):
        if label in dstr:
            return code
    if "检定" in dstr:
        return "检定"
    if "校准" in dstr:
        return "校准"
    if "校验" in dstr:
        return "校验"
    return ""


def recognize_calibration_tool_name(lines: list[str]) -> str:
    for i, line in enumerate(lines):
        if "名称" not in line:
            continue
        if "北京京港地铁有限公司" in line:
            continue
        if i + 1 < len(lines) and "北京京港地铁有限公司" in lines[i + 1]:
            continue
        parts = [p for p in line.split("名称：") if p]
        if "名称:" in line and len(parts) <= 1:
            parts = [p for p in line.split("名称:") if p]
        if len(parts) > 1:
            return _remove_punctuation(parts[-1])
        if i + 1 < len(lines):
            return _remove_punctuation(lines[i + 1])
    return ""


def _sub_digits_between(blob: str, start_key: str, end_key: str) -> list[str]:
    start = blob.find(start_key)
    end = blob.find(end_key)
    if start < 0 or end < 0 or end <= start:
        return []
    start += len(start_key)
    numbers = re.findall(r"\d+", blob[start:end])
    if not numbers:
        return []
    return [f"{start_key}{''.join(numbers)}"]


def _find_dates_and_indices(blob: str) -> list[str]:
    starts = ("校准期", "检定期", "校准日期", "检定日期")
    ends = ("发布期", "有效期", "建议下", "发布日期")
    for sub_s in starts:
        for sub_e in ends:
            if sub_s in blob and sub_e in blob:
                res = _sub_digits_between(blob, sub_s, sub_e)
                if res:
                    return res
    # Fallback: first YYYYMMDD / YYYY年MM月DD日 after a date label.
    m = re.search(
        r"(?:校准|检定|检测)日期[:：]?\s*(\d{4})\s*年?\s*(\d{1,2})\s*月?\s*(\d{1,2})",
        blob,
    )
    if m:
        return [f"校准期{int(m.group(1)):04d}{int(m.group(2)):02d}{int(m.group(3)):02d}"]
    m = re.search(r"(?:校准|检定|检测)日期[:：]?\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", blob)
    if m:
        return [f"校准期{int(m.group(1)):04d}{int(m.group(2)):02d}{int(m.group(3)):02d}"]
    return []


def _date_output(token: str) -> str:
    if token.startswith(("校准期", "检定期")) and len(token) >= 11:
        y, m, d = token[3:7], token[7:9], token[9:11]
        return f"{y}-{m}-{d}"
    if "效期至" in token and len(token) >= 11:
        # ji_liang: due date token → previous day + 1 year logic inverted to measurement date
        y = int(token[3:7]) - 1
        m = int(token[7:9])
        d = int(token[9:11])
        date0 = datetime.datetime(year=y, month=m, day=d) + timedelta(days=1)
        return f"{date0.year}-{date0.month:02d}-{date0.day:02d}"
    return ""


def recognize_calibration_date(lines: list[str]) -> str:
    blob = _remove_punctuation("".join(lines))
    blob = blob.replace("年", "").replace("月", "").replace("日", "")
    # Keep a second pass on original-ish text for ISO / 年月日 forms.
    joined = "".join(lines)
    res = _find_dates_and_indices(joined) or _find_dates_and_indices(blob)
    if not res:
        m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", joined)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", joined)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return ""
    return _date_output(res[0])


def recognize_calibration_model(lines: list[str]) -> str:
    blob = "".join(lines)
    index = blob.find("型号")
    if index == -1:
        return ""
    chunk = blob[index : index + 24]
    for token in ("型号", "/", "：", ":", "物品", "编", "规格制造厂", "规格"):
        chunk = chunk.replace(token, "")
    for token in ("出厂", "NAME", "制造", "器具", "Mod", "制造厂"):
        chunk = chunk.replace(token, "====")
    chunk = chunk.split("====")[0]
    chunk = chunk.replace("（", "(").replace("）", ")").replace("～", "~")
    return chunk.strip()


def recognize_calibration_no(lines: list[str]) -> str:
    blob = "====".join(lines)
    for token in ("证书编号", "====管理号：", "====管理号", "====电", "====卡片号"):
        blob = blob.replace(token, "")
    blob = blob.replace("序号", "编号").replace("编====号", "编号")
    blob = blob.replace("管理编号：", "管理编号====")
    index = blob.find("编号")
    if index == -1:
        # Common OCR: 出厂编号 on its own line, value next.
        for i, line in enumerate(lines):
            if "出厂编号" in line or "器具编号" in line or "管理编号" in line:
                rest = re.sub(r"^.*编号[:：]?\s*", "", line).strip()
                if rest and rest not in {"出厂", "器具", "管理"}:
                    return re.sub(r"\s+", "", rest)
                if i + 1 < len(lines):
                    return re.sub(r"\s+", "", lines[i + 1])
        return ""
    chunk = blob[max(0, index - 2) : index + 28]
    parts = chunk.split("====")
    if len(parts) > 1:
        return re.sub(r"\s+", "", parts[1])
    return ""


def parse_ocr_lines(lines: list[str]) -> CertificateFields:
    """Extract the six core metrology fields from OCR cover lines."""
    clean = [ln.strip() for ln in lines if ln and ln.strip()]
    dstr = "".join(clean)
    fields = CertificateFields(
        name=_strip_fail(recognize_calibration_tool_name(clean)),
        serial_num=_strip_fail(recognize_calibration_no(clean)),
        model=_strip_fail(recognize_calibration_model(clean)),
        measurement_unit=_strip_fail(recognize_calibration_unit(dstr)),
        measurement_date=_strip_fail(recognize_calibration_date(clean)),
        measurement_type=_strip_fail(recognize_calibration_type(dstr)),
    )
    return fields
