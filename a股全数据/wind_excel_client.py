from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Sequence

import pandas as pd
import pythoncom
import win32com.client as win32


class WindExcelError(RuntimeError):
    pass


class WindQuotaError(WindExcelError):
    pass


QUOTA_MARKER = "\u63d0\u53d6\u6570\u636e\u8d85\u9650"

_ERROR_MARKERS = (
    "Fetching...",
    "No Python API",
    "???",
    "??",
    "??????",
    "??",
    "failed",
    "Failed",
)


def _as_2d(value: Any) -> list[list[Any]]:
    if value is None:
        return []
    if isinstance(value, tuple):
        if not value:
            return []
        if isinstance(value[0], tuple):
            return [list(x) for x in value]
        return [list(value)]
    return [[value]]


def excel_dt(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time())
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value > 1:
            return dt.datetime(1899, 12, 30) + dt.timedelta(days=float(value))
        return None
    s = str(value).strip()
    if not s or s.lower() == "none":
        return None
    if "Fetching" in s or "??" in s or "????" in s:
        return None
    try:
        out = pd.to_datetime(s, errors="raise").to_pydatetime()
        return out.replace(tzinfo=None)
    except Exception:
        return None


def excel_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _normalize_cell(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return excel_dt(value)
    return value


class WindExcelClient:
    def __init__(self, logger: logging.Logger | None = None, visible: bool = False):
        self.log = logger or logging.getLogger("wind_excel")
        self.visible = visible
        self.app = None
        self.wb = None
        self.ws = None
        self.restart_count = 0
        pythoncom.CoInitialize()
        self._start()

    def _start(self) -> None:
        self.app = win32.DispatchEx("Excel.Application")
        self.app.Visible = self.visible
        self.app.DisplayAlerts = False
        self.app.EnableEvents = False
        self.app.ScreenUpdating = False
        self.wb = self.app.Workbooks.Add()
        self.ws = self.wb.Worksheets(1)
        try:
            self.ws.Name = "WIND"
        except Exception:
            pass

    def close(self) -> None:
        try:
            if self.wb is not None:
                self.wb.Close(SaveChanges=False)
        except Exception:
            pass
        try:
            if self.app is not None:
                self.app.Quit()
        except Exception:
            pass
        self.wb = None
        self.ws = None
        self.app = None
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    def restart(self) -> None:
        self.log.warning("restarting hidden Excel/WDF instance")
        self.close()
        self.restart_count += 1
        pythoncom.CoInitialize()
        self._start()

    def _clear(self) -> None:
        try:
            self.ws.UsedRange.Clear()
        except Exception:
            try:
                self.ws.Cells.Clear()
            except Exception:
                pass

    def _cell(self, row: int = 1, col: int = 1) -> Any:
        return self.ws.Cells(row, col).Value

    def _wait_for_result(
        self,
        timeout: float,
        expected_rows: int | None = None,
        expected_cols: int | None = None,
        allow_single_cell: bool = False,
    ) -> tuple[int, int, Any]:
        deadline = time.monotonic() + timeout
        last_seen = None
        stable = 0
        while time.monotonic() < deadline:
            used = self.ws.UsedRange
            rows = int(used.Rows.Count)
            cols = int(used.Columns.Count)
            first = self._cell(1, 1)
            text = excel_text(first)
            if "Fetching" in text:
                last_seen = text
                stable = 0
                time.sleep(0.2)
                continue
            if QUOTA_MARKER in text:
                raise WindQuotaError(text)
            if text in _ERROR_MARKERS or any(m in text for m in _ERROR_MARKERS if m != "Fetching..."):
                if rows == 1 and cols == 1:
                    raise WindExcelError(text)
            if allow_single_cell and rows == 1 and cols == 1 and first is not None:
                return rows, cols, first
            if expected_rows is not None and expected_cols is not None:
                if rows >= expected_rows and cols >= expected_cols:
                    stable += 1
                    if stable >= 2:
                        return rows, cols, first
                else:
                    stable = 0
            else:
                if rows > 1 or cols > 1:
                    stable += 1
                    if stable >= 2:
                        return rows, cols, first
                elif first is not None and text and "Fetching" not in text:
                    return 1, 1, first
            time.sleep(0.2)
        raise TimeoutError(f"Wind Excel query timed out after {timeout:.0f}s; last={last_seen!r}")

    def _read(self, rows: int, cols: int) -> list[list[Any]]:
        raw = self.ws.Range(self.ws.Cells(1, 1), self.ws.Cells(rows, cols)).Value
        return _as_2d(raw)

    def query_wset(self, table: str, options: str, timeout: float = 120.0, expected_rows: int | None = None) -> pd.DataFrame:
        formula = f'=WSET("{table}","{options}")'
        self._clear()
        self.ws.Cells(1, 1).Formula = formula
        if expected_rows is not None:
            rows, cols, first = self._wait_for_result(timeout, expected_rows=max(1, expected_rows - 1), expected_cols=1)
        else:
            rows, cols, first = self._wait_for_result(timeout)
        if rows <= 1 and cols <= 1 and first is None:
            return pd.DataFrame()
        data = self._read(rows, cols)
        return pd.DataFrame([[_normalize_cell(x) for x in row] for row in data])

    def query_wss(self, codes: Sequence[str], fields: Sequence[str], timeout: float = 120.0) -> pd.DataFrame:
        if not codes or not fields:
            return pd.DataFrame()
        formula = f'=WSS("{",".join(codes)}","{",".join(fields)}")'
        if len(formula) > 7500:
            raise ValueError(f"WSS formula too long: {len(formula)} characters")
        self._clear()
        self.ws.Cells(1, 1).Formula = formula
        rows, cols, _ = self._wait_for_result(timeout, expected_rows=len(codes), expected_cols=len(fields))
        data = self._read(max(rows, len(codes)), max(cols, len(fields)))
        data = data[: len(codes)]
        data = [[_normalize_cell(x) for x in (r[: len(fields)] + [None] * max(0, len(fields) - len(r)))] for r in data]
        return pd.DataFrame(data, columns=list(fields))

    def query_wsi(
        self,
        symbol: str,
        fields: Sequence[str],
        start: dt.datetime,
        end: dt.datetime,
        timeout: float = 120.0,
    ) -> pd.DataFrame:
        field_text = ",".join(fields)
        start_text = start.strftime("%Y-%m-%d %H:%M:%S")
        end_text = end.strftime("%Y-%m-%d %H:%M:%S")
        formula = f'=WSI("{symbol}","{field_text}","{start_text}","{end_text}","BarSize=1")'
        self._clear()
        self.ws.Cells(1, 1).Formula = formula
        try:
            rows, cols, first = self._wait_for_result(timeout, allow_single_cell=True)
        except WindExcelError as exc:
            if "??????" in str(exc):
                return pd.DataFrame(columns=["datetime", *fields])
            raise
        if rows <= 1 and not isinstance(first, (dt.datetime, dt.date, int, float)):
            return pd.DataFrame(columns=["datetime", *fields])
        data = self._read(rows, max(cols, len(fields) + 1))
        clean = []
        for row in data:
            if not row:
                continue
            ts = excel_dt(row[0])
            if ts is None:
                continue
            clean.append([ts, *row[1 : len(fields) + 1]])
        if not clean:
            return pd.DataFrame(columns=["datetime", *fields])
        return pd.DataFrame(clean, columns=["datetime", *fields])

    def query_wsd_fields(
        self,
        symbol: str,
        fields: Sequence[str],
        start: dt.date,
        end: dt.date,
        options: str | None = None,
        timeout: float = 120.0,
    ) -> pd.DataFrame:
        field_text = ",".join(fields)
        opt = f',"{options}"' if options else ""
        formula = f'=WSD("{symbol}","{field_text}","{start:%Y-%m-%d}","{end:%Y-%m-%d}"{opt})'
        if len(formula) > 7500:
            raise ValueError(f"WSD formula too long: {len(formula)} characters")
        self._clear()
        self.ws.Cells(1, 1).Formula = formula
        try:
            rows, cols, _ = self._wait_for_result(timeout)
        except WindExcelError as exc:
            if "??????" in str(exc):
                return pd.DataFrame(columns=["date", *fields])
            raise
        if rows <= 1 and cols <= 1:
            return pd.DataFrame(columns=["date", *fields])
        data = self._read(rows, max(cols, len(fields) + 1))
        clean = []
        for row in data:
            if not row:
                continue
            date = excel_dt(row[0])
            if date is None:
                continue
            clean.append([date.date(), *row[1 : len(fields) + 1]])
        return pd.DataFrame(clean, columns=["date", *fields])

    def query_wsd_codes(
        self,
        codes: Sequence[str],
        field: str,
        start: dt.date,
        end: dt.date,
        options: str | None = None,
        timeout: float = 120.0,
    ) -> pd.DataFrame:
        opt = f',"{options}"' if options else ""
        formula = f'=WSD("{",".join(codes)}","{field}","{start:%Y-%m-%d}","{end:%Y-%m-%d}"{opt})'
        if len(formula) > 7500:
            raise ValueError(f"WSD formula too long: {len(formula)} characters")
        self._clear()
        self.ws.Cells(1, 1).Formula = formula
        try:
            rows, cols, _ = self._wait_for_result(timeout, expected_rows=1, expected_cols=len(codes))
        except WindExcelError as exc:
            if "??????" in str(exc):
                return pd.DataFrame(columns=list(codes))
            raise
        data = self._read(rows, max(cols, len(codes)))
        data = [r[: len(codes)] + [None] * max(0, len(codes) - len(r)) for r in data[:rows]]
        return pd.DataFrame(data, columns=list(codes))
