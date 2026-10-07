"""exports.py -- Excel / CSV bytes for the dashboard's current (filtered) view."""
import io

import pandas as pd
from openpyxl.utils import get_column_letter

_WIDTHS = {"title": 70, "detail": 50, "url": 45, "matched": 30, "agency": 30, "notice_id": 30}


def spreadsheet_safe(frame: pd.DataFrame) -> pd.DataFrame:
    """Scraped text is untrusted: stop a title like '=HYPERLINK(...)' from
    becoming a live formula when the export is opened in Excel."""
    return frame.map(lambda v: "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v)


def to_xlsx(frame: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        frame.to_excel(xw, index=False, sheet_name="RFPs")
        ws = xw.sheets["RFPs"]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for i, col in enumerate(frame.columns, start=1):
            ws.column_dimensions[get_column_letter(i)].width = _WIDTHS.get(col, 16)
    return buf.getvalue()


def to_csv(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8-sig")  # BOM so Excel reads accents correctly
