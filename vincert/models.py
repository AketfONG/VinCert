from __future__ import annotations #loose order of declaration and definition

from dataclasses import asdict, dataclass, field


@dataclass
class CertificateFields:
    """Structured fields extracted from a metrology certificate."""

    name: str = ""  # 计量器具名称
    serial_num: str = ""  # 计量器具编号 / 出厂编号
    model: str = ""  # 型号/规格
    measurement_unit: str = ""  # 检测机构
    measurement_date: str = ""  # 本次检测日期 YYYY-MM-DD
    measurement_type: str = ""  # 检验方式：检定 / 校准 / 校验
    certificate_no: str = ""  # 证书编号
    client_name: str = ""  # 客户名称
    manufacturer: str = ""  # 制造厂
    due_date: str = ""  # 本次检测有效期至
    issue_date: str = ""  # 发布日期

    def match_fields(self) -> dict[str, str]:
        """Fields used to verify the correct webpage record before autofill."""
        return {
            "name": self.name,
            "serial_num": self.serial_num,
            "manufacturer": self.manufacturer,
        }

    def autofill_fields(self) -> dict[str, str]:
        """Fields written into the target webpage form."""
        return {
            "measurement_type": self.measurement_type,
            "measurement_date": self.measurement_date,
            "due_date": self.due_date,
            "measurement_unit": self.measurement_unit,
        }

    def as_display_dict(self) -> dict[str, str]:
        labels = {
            "name": "计量器具名称",
            "serial_num": "计量器具编号",
            "manufacturer": "制造厂",
            "measurement_type": "检验方式",
            "measurement_date": "本次检测日期",
            "due_date": "本次检测有效期至",
            "measurement_unit": "检测机构",
            "model": "型号/规格",
            "certificate_no": "证书编号",
            "client_name": "客户名称",
            "issue_date": "发布日期",
        }
        data = asdict(self)
        return {labels[k]: data[k] for k in labels}

    def format_readable(self) -> str:
        lines = [f"{label}: {value or '—'}" for label, value in self.as_display_dict().items()]
        return "\n".join(lines)


@dataclass
class ParseResult:
    source_path: str
    page_count: int
    raw_text: str
    lines: list[str]
    fields: CertificateFields
    method: str = "embedded_text"  # embedded_text | ocr
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.fields.name or self.fields.serial_num or self.fields.certificate_no)
