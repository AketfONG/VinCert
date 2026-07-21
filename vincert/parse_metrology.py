"""Field parsers for metrology certificates (digital PDF text)."""

from __future__ import annotations

import re

from .models import CertificateFields

# Labels that mark the start of a field value block on 天溯-style certificates.
LABEL_ALIASES: dict[str, tuple[str, ...]] = {
    "certificate_no": ("证书编号",),
    "client_name": ("客户名称",),
    "name": ("仪器名称", "器具名称", "计量器具名称"),
    "model": ("型号/规格", "型号规格", "型号：", "型号"),
    "manufacturer": ("制造厂商", "制造厂"),
    "serial_num": ("出厂编号", "计量器具编号"),
    "management_no": ("管理编号",),
    "receipt_date": ("接收日期",),
    "measurement_date": ("校准日期", "检定日期", "本次检测日期", "检测日期"),
    "due_date": (
        "建议下次校准日期",
        "建议下次检定日期",
        "有效期至",
        "本次检测有效期至",
        "检测有效期至",
    ),
    "issue_date": ("发布日期",),
}

# English / noise lines that sit between Chinese labels and values.
SKIP_LINE_RE = re.compile(
    r"^(?:"
    r"Client Name|Address|Description|Model/?Type|Manufacturer|"
    r"Serial Number|Management No\.?|Date of Receipt|Calibration Date|"
    r"Due Date|Issue Date|Certi.icate No\.?|Page|of|"
    r"Year|Month|Day|"
    r"Approved by|Inspected by|Calibrated by|"
    r"第\s*\d+\s*页|共\s*\d+\s*页"
    r")$",
    re.IGNORECASE,
)

DATE_TOKEN_RE = re.compile(
    r"(?P<y>\d{4})\s*年\s*(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日"
)
DATE_ISO_RE = re.compile(r"(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})")


def normalize_spaced_cjk(text: str) -> str:
    """Collapse readability spaces inside CJK runs: '校 准 证 书' -> '校准证书'."""

    def _collapse(match: re.Match[str]) -> str:
        return re.sub(r"\s+", "", match.group(0))

    # Sequences of CJK chars separated by single spaces
    return re.sub(
        r"(?:[\u4e00-\u9fff]\s+){1,}[\u4e00-\u9fff]",
        _collapse,
        text,
    )


def clean_lines(raw_text: str) -> list[str]:
    lines: list[str] = []
    for line in raw_text.splitlines():
        line = normalize_spaced_cjk(line.strip())
        # Also collapse odd double-spaces in labels like "地  址"
        line = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", line)
        if line:
            lines.append(line)
    return lines


def _is_noise(line: str) -> bool:
    if SKIP_LINE_RE.match(line):
        return True
    if re.fullmatch(r"[/—\-–]+", line):
        return True
    return False


def _label_match_len(line: str, alias: str) -> int:
    """Return matched alias length if line is that label (exact or alias + delimiter)."""
    if line == alias:
        return len(alias)
    if line.startswith(alias):
        rest = line[len(alias) :]
        # Require a clear delimiter so "型号" does not eat "型号/规格"
        if rest[:1] in {"：", ":", " ", "\t", ".", "．"}:
            return len(alias)
    return 0


def _find_label_index(lines: list[str], aliases: tuple[str, ...]) -> int | None:
    # Longest alias first so "型号/规格" wins over "型号"
    ordered = sorted(aliases, key=len, reverse=True)
    for i, line in enumerate(lines):
        for alias in ordered:
            if _label_match_len(line, alias):
                return i
    return None


def _matched_alias(line: str, aliases: tuple[str, ...]) -> str | None:
    for alias in sorted(aliases, key=len, reverse=True):
        if _label_match_len(line, alias):
            return alias
    return None


def _is_label_line(line: str, stop_labels: set[str]) -> bool:
    if line in stop_labels:
        return True
    for lab in sorted(stop_labels, key=len, reverse=True):
        if _label_match_len(line, lab):
            return True
    return False


def _value_after_label(
    lines: list[str],
    start: int,
    *,
    aliases: tuple[str, ...],
    stop_labels: set[str],
) -> str:
    """Collect the first meaningful value after a label line."""
    label_line = lines[start]
    alias = _matched_alias(label_line, aliases)
    if alias and len(label_line) > len(alias):
        rest = label_line[len(alias) :].lstrip(" ：:.\t")
        if rest and not _is_noise(rest):
            return rest.strip()

    for j in range(start + 1, len(lines)):
        candidate = lines[j]
        if _is_label_line(candidate, stop_labels):
            break
        if _is_noise(candidate):
            continue
        # Skip pure English company lines that follow Chinese issuer names occasionally
        if candidate.isascii() and not re.search(r"\d", candidate) and len(candidate) > 20:
            continue
        return candidate.strip()
    return ""


def _all_stop_labels() -> set[str]:
    labels: set[str] = set()
    for aliases in LABEL_ALIASES.values():
        labels.update(aliases)
    labels.update({"地址", "批准:", "批准：", "核验:", "核验：", "校准:", "校准：", "检定:", "检定："})
    return labels


def parse_date_near(lines: list[str], start: int, window: int = 8) -> str:
    """Parse a date from the block following a date label (handles split 年/月/日 lines)."""
    chunk = " ".join(lines[start : start + window + 1])
    chunk = normalize_spaced_cjk(chunk)
    # Rebuild if year/month/day are on separate lines with English noise between
    m = DATE_TOKEN_RE.search(chunk)
    if m:
        return f"{int(m['y']):04d}-{int(m['m']):02d}-{int(m['d']):02d}"

    # Reconstruct from tokens: "2026 年" "07 月" "09 日"
    y = mth = d = None
    for line in lines[start : start + window + 1]:
        if y is None:
            ym = re.search(r"(\d{4})\s*年", line)
            if ym:
                y = int(ym.group(1))
                continue
        if y is not None and mth is None:
            mm = re.search(r"(\d{1,2})\s*月", line)
            if mm:
                mth = int(mm.group(1))
                continue
        if y is not None and mth is not None and d is None:
            dm = re.search(r"(\d{1,2})\s*日", line)
            if dm:
                d = int(dm.group(1))
                break
    if y and mth and d:
        return f"{y:04d}-{mth:02d}-{d:02d}"

    m2 = DATE_ISO_RE.search(chunk)
    if m2:
        return f"{int(m2['y']):04d}-{int(m2['m']):02d}-{int(m2['d']):02d}"
    return ""


def detect_type(lines: list[str], raw: str) -> str:
    joined = "".join(lines) + raw
    if "检定证书" in joined or re.search(r"检\s*定\s*证\s*书", raw):
        return "检定"
    if "校准证书" in joined or re.search(r"校\s*准\s*证\s*书", raw):
        return "校准"
    if "校验证书" in joined or re.search(r"校\s*验\s*证\s*书", raw):
        return "校验"
    if "校验" in joined:
        return "校验"
    if "校准" in joined:
        return "校准"
    if "检定" in joined:
        return "检定"
    return ""


def detect_unit(lines: list[str]) -> str:
    """Issuer / measurement unit — usually the first Chinese company-like line."""
    for line in lines[:6]:
        if "有限公司" in line or "研究院" in line or "研究所" in line or "检测" in line:
            if line.isascii():
                continue
            return line
    return lines[0] if lines else ""


def parse_fields(raw_text: str) -> CertificateFields:
    lines = clean_lines(raw_text)
    stops = _all_stop_labels()
    fields = CertificateFields()

    fields.measurement_type = detect_type(lines, raw_text)
    fields.measurement_unit = detect_unit(lines)

    for key, aliases in LABEL_ALIASES.items():
        idx = _find_label_index(lines, aliases)
        if idx is None:
            continue
        if key in {"receipt_date", "measurement_date", "due_date", "issue_date"}:
            value = parse_date_near(lines, idx)
        else:
            value = _value_after_label(lines, idx, aliases=aliases, stop_labels=stops)
            if key == "serial_num":
                # Normalize odd spaces in serials: "KA425- 8095623"
                value = re.sub(r"\s+", "", value)
            if key == "model":
                value = value.replace("～", "~")
        if hasattr(fields, key):
            setattr(fields, key, value)

    return fields
