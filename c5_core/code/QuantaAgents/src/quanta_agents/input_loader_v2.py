from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml


def _read_source_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".csv", ".yaml", ".yml"}:
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except Exception as exc:  # pragma: no cover - 可选依赖
            raise RuntimeError("读取PDF研报需要安装pypdf") from exc
        return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    if suffix == ".docx":
        try:
            from docx import Document
        except Exception as exc:  # pragma: no cover - 可选依赖
            raise RuntimeError("读取Word研报需要安装python-docx") from exc
        document = Document(str(path))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    raise ValueError(f"暂不支持的输入文件: {suffix or '<none>'}")


def load_v2_request(path: str | Path) -> dict[str, Any]:
    request_path = Path(path).resolve()
    parsed = yaml.safe_load(request_path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("V2输入文件必须是YAML对象")
    if str(parsed.get("workflow_version", "v2")).strip().lower() != "v2":
        raise ValueError("workflow_version 必须是 v2")

    direct = next(
        (
            str(parsed[key]).strip()
            for key in ("user_idea", "idea", "source_text")
            if isinstance(parsed.get(key), str) and str(parsed[key]).strip()
        ),
        "",
    )
    source_file = parsed.get("source_file")
    if direct and source_file:
        raise ValueError("一句话和source_file只能选一种")
    if not direct and isinstance(source_file, str) and source_file.strip():
        resolved_file = Path(source_file).expanduser()
        if not resolved_file.is_absolute():
            resolved_file = request_path.parent / resolved_file
        direct = _read_source_file(resolved_file.resolve()).strip()
        source_kind = "report_file"
        source_path = str(resolved_file.resolve())
    else:
        source_kind = "plain_text"
        source_path = str(request_path)
    if not direct:
        raise ValueError("V2输入没有可研究的文字")
    return {
        "workflow_version": "v2",
        "source_text": direct,
        "source_kind": source_kind,
        "source_path": source_path,
        "source_sha256": sha256(direct.encode("utf-8")).hexdigest(),
    }
