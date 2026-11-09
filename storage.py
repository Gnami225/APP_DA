"""Stockage des données : Google Sheet (production) ou fichier Excel local (test / secours).

Les deux backends exposent la même interface :
    read(table) -> DataFrame
    append(table, rows: list[dict])
    update(table, key_col, key, values: dict)
"""
from __future__ import annotations

import threading
from pathlib import Path

import pandas as pd

from schema import TABLES


def _frame(rows: list[list], header: list[str], table: str) -> pd.DataFrame:
    cols = TABLES.get(table, header)
    df = pd.DataFrame(rows, columns=header) if rows else pd.DataFrame(columns=header)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df.fillna("")
    # lignes complètement vides (fréquentes en fin de feuille) écartées
    if len(df):
        vide = df.astype(str).apply(lambda r: "".join(r).strip(), axis=1) == ""
        df = df.loc[~vide.astype(bool)]
    return df.reset_index(drop=True)


def _clean(v):
    """Valeur prête à écrire : nombres en nombres, le reste en texte."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return v
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _fix_key(key: str) -> str:
    """Répare les erreurs de copie courantes de la clé privée."""
    key = str(key).strip().strip('"').strip("'")
    key = key.replace("\\n", "\n").replace("\r", "")
    head, tail = "-----BEGIN PRIVATE KEY-----", "-----END PRIVATE KEY-----"
    if head in key and tail in key:
        body = key.split(head, 1)[1].split(tail, 1)[0]
        body = "".join(body.split())  # retire espaces et retours parasites
        lines = [body[i:i + 64] for i in range(0, len(body), 64)]
        key = head + "\n" + "\n".join(lines) + "\n" + tail + "\n"
    return key


class GoogleSheetStore:
    """Backend Google Sheets via un compte de service."""

    def __init__(self, service_account_info: dict, sheet_id: str):
        import gspread
        from google.oauth2.service_account import Credentials

        info = dict(service_account_info)
        info["private_key"] = _fix_key(info.get("private_key", ""))
        creds = Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        self.gc = gspread.authorize(creds)
        self.sh = self.gc.open_by_key(sheet_id)
        self._ws: dict = {}
        self._headers: dict[str, list[str]] = {}
        self.ensure_schema()

    def _sheet(self, table: str):
        if table not in self._ws:
            self._ws[table] = self.sh.worksheet(table)
        return self._ws[table]

    def ensure_schema(self):
        import gspread

        existing = {ws.title for ws in self.sh.worksheets()}
        for table, cols in TABLES.items():
            if table not in existing:
                ws = self.sh.add_worksheet(table, rows=1000, cols=max(len(cols), 10))
                ws.update([cols], "A1")
                ws.freeze(rows=1)
            ws = self._sheet(table)
            header = ws.row_values(1)
            missing = [c for c in cols if c not in header]
            if missing:
                start = len(header) + 1
                if ws.col_count < start + len(missing) - 1:
                    ws.add_cols(start + len(missing) - 1 - ws.col_count)
                ws.update([missing], gspread.utils.rowcol_to_a1(1, start))
                header = header + missing
            self._headers[table] = header

    def read(self, table: str) -> pd.DataFrame:
        ws = self._sheet(table)
        from gspread.utils import ValueRenderOption

        values = ws.get_all_values(value_render_option=ValueRenderOption.unformatted)
        if not values:
            return _frame([], TABLES[table], table)
        header = [str(h) for h in values[0]]
        width = len(header)
        rows = [(r + [""] * width)[:width] for r in values[1:]]
        return _frame(rows, header, table)

    def append(self, table: str, rows: list[dict]):
        if not rows:
            return
        header = self._headers[table]
        data = [[_clean(r.get(c, "")) for c in header] for r in rows]
        from gspread.utils import InsertDataOption, ValueInputOption

        self._sheet(table).append_rows(data, value_input_option=ValueInputOption.raw,
                                       insert_data_option=InsertDataOption.insert_rows,
                                       table_range="A1")

    def update(self, table: str, key_col: str, key: str, values: dict):
        import gspread

        ws = self._sheet(table)
        header = self._headers[table]
        keys = ws.col_values(header.index(key_col) + 1)
        try:
            row = keys.index(str(key)) + 1
        except ValueError as exc:
            raise KeyError(f"{key} introuvable dans {table}") from exc
        batch = []
        for col, val in values.items():
            a1 = gspread.utils.rowcol_to_a1(row, header.index(col) + 1)
            batch.append({"range": a1, "values": [[_clean(val)]]})
        ws.batch_update(batch)  # raw=True par défaut : valeurs écrites telles quelles


class ExcelStore:
    """Backend fichier Excel local : tests, ou serveur sur un PC du bureau."""

    _lock = threading.Lock()

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            self._create()
        self.ensure_schema()

    def _create(self):
        from openpyxl import Workbook

        wb = Workbook()
        wb.remove(wb.active)
        for table, cols in TABLES.items():
            ws = wb.create_sheet(table)
            ws.append(cols)
        wb.save(self.path)

    def ensure_schema(self):
        from openpyxl import load_workbook

        with self._lock:
            wb = load_workbook(self.path)
            changed = False
            for table, cols in TABLES.items():
                if table not in wb.sheetnames:
                    wb.create_sheet(table).append(cols)
                    changed = True
                    continue
                ws = wb[table]
                header = [c.value for c in ws[1]]
                for c in cols:
                    if c not in header:
                        ws.cell(1, len(header) + 1, c)
                        header.append(c)
                        changed = True
            if changed:
                wb.save(self.path)

    def read(self, table: str) -> pd.DataFrame:
        from openpyxl import load_workbook

        with self._lock:
            wb = load_workbook(self.path, read_only=True, data_only=True)
            rows = [list(r) for r in wb[table].iter_rows(values_only=True)]
            wb.close()
        if not rows:
            return _frame([], TABLES[table], table)
        header = [str(h) for h in rows[0]]
        body = [[("" if v is None else v) for v in r] for r in rows[1:]]
        return _frame(body, header, table)

    def append(self, table: str, rows: list[dict]):
        from openpyxl import load_workbook

        with self._lock:
            wb = load_workbook(self.path)
            ws = wb[table]
            header = [c.value for c in ws[1]]
            for r in rows:
                ws.append([_clean(r.get(c, "")) for c in header])
            wb.save(self.path)

    def update(self, table: str, key_col: str, key: str, values: dict):
        from openpyxl import load_workbook

        with self._lock:
            wb = load_workbook(self.path)
            ws = wb[table]
            header = [c.value for c in ws[1]]
            kidx = header.index(key_col) + 1
            for row in range(2, ws.max_row + 1):
                if str(ws.cell(row, kidx).value) == str(key):
                    for col, val in values.items():
                        ws.cell(row, header.index(col) + 1, _clean(val))
                    wb.save(self.path)
                    return
            raise KeyError(f"{key} introuvable dans {table}")
