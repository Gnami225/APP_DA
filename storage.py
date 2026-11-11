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


def _retry(fn, *args, **kwargs):
    """Rejoue l'appel si Google répond « trop de requêtes » (429) ou erreur passagère (5xx)."""
    import time
    from gspread.exceptions import APIError

    for attempt in range(6):
        try:
            return fn(*args, **kwargs)
        except APIError as exc:
            if getattr(exc, "code", 0) not in (429, 500, 502, 503) or attempt == 5:
                raise
            time.sleep(min(2 ** attempt, 30))


class GoogleSheetStore:
    """Backend Google Sheets via un compte de service. Une seule lecture groupée par rafraîchissement."""

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
        self.sh = _retry(self.gc.open_by_key, sheet_id)
        self._ws: dict = {ws.title: ws for ws in self.sh.worksheets()}  # pas d'appel : métadonnées déjà chargées
        self._headers: dict[str, list[str]] = {}
        self.ensure_schema()

    @staticmethod
    def _rng(table: str, rows: str = "") -> str:
        return f"'{table}'" + (f"!{rows}" if rows else "")

    def ensure_schema(self):
        import gspread

        for table, cols in TABLES.items():
            if table not in self._ws:
                ws = _retry(self.sh.add_worksheet, table, rows=1000, cols=max(len(cols), 10))
                _retry(ws.update, [cols], "A1")
                self._ws[table] = ws
        res = _retry(self.sh.values_batch_get, [self._rng(t, "1:1") for t in TABLES])
        for table, vr in zip(TABLES, res.get("valueRanges", [])):
            header = [str(h) for h in (vr.get("values") or [[]])[0]]
            missing = [c for c in TABLES[table] if c not in header]
            if missing:
                ws = self._ws[table]
                start = len(header) + 1
                need = start + len(missing) - 1
                if ws.col_count < need:
                    _retry(ws.add_cols, need - ws.col_count)
                _retry(ws.update, [missing], gspread.utils.rowcol_to_a1(1, start))
                header = header + missing
            self._headers[table] = header

    def read_all(self) -> dict[str, pd.DataFrame]:
        res = _retry(self.sh.values_batch_get, [self._rng(t) for t in TABLES],
                     params={"valueRenderOption": "UNFORMATTED_VALUE"})
        out = {}
        for table, vr in zip(TABLES, res.get("valueRanges", [])):
            values = vr.get("values") or []
            if not values:
                out[table] = _frame([], TABLES[table], table)
                continue
            header = [str(h) for h in values[0]]
            width = len(header)
            rows = [(list(r) + [""] * width)[:width] for r in values[1:]]
            out[table] = _frame(rows, header, table)
        return out

    def read(self, table: str) -> pd.DataFrame:
        return self.read_all()[table]

    def append(self, table: str, rows: list[dict]):
        if not rows:
            return
        from gspread.utils import InsertDataOption, ValueInputOption

        header = self._headers[table]
        data = [[_clean(r.get(c, "")) for c in header] for r in rows]
        _retry(self._ws[table].append_rows, data, value_input_option=ValueInputOption.raw,
               insert_data_option=InsertDataOption.insert_rows, table_range="A1")

    def update(self, table: str, key_col: str, key: str, values: dict):
        import gspread

        ws = self._ws[table]
        header = self._headers[table]
        keys = _retry(ws.col_values, header.index(key_col) + 1)
        try:
            row = [str(k) for k in keys].index(str(key)) + 1
        except ValueError as exc:
            raise KeyError(f"{key} introuvable dans {table}") from exc
        batch = []
        for col, val in values.items():
            a1 = gspread.utils.rowcol_to_a1(row, header.index(col) + 1)
            batch.append({"range": a1, "values": [[_clean(val)]]})
        _retry(ws.batch_update, batch)  # raw=True par défaut : valeurs écrites telles quelles

    def rewrite(self, table: str, df: pd.DataFrame):
        """Réécrit tout l'onglet avec les lignes de df (archivage annuel)."""
        header = self._headers[table]
        data = [header] + [[_clean(r.get(c, "")) for c in header] for r in df.to_dict("records")]
        ws = self._ws[table]
        _retry(ws.batch_clear, ["A2:ZZ"])
        _retry(ws.update, data, "A1", raw=True)

    def update_many(self, table: str, key_col: str, changes: dict[str, dict]):
        """Plusieurs lignes en 2 appels Google : lecture des clés, puis écriture groupée."""
        if not changes:
            return
        import gspread

        ws = self._ws[table]
        header = self._headers[table]
        keys = [str(k) for k in _retry(ws.col_values, header.index(key_col) + 1)]
        batch = []
        for key, values in changes.items():
            try:
                row = keys.index(str(key)) + 1
            except ValueError as exc:
                raise KeyError(f"{key} introuvable dans {table}") from exc
            for col, val in values.items():
                a1 = gspread.utils.rowcol_to_a1(row, header.index(col) + 1)
                batch.append({"range": a1, "values": [[_clean(val)]]})
        _retry(ws.batch_update, batch)


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

    def read_all(self) -> dict[str, pd.DataFrame]:
        return {t: self.read(t) for t in TABLES}

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

    def update_many(self, table: str, key_col: str, changes: dict[str, dict]):
        for key, values in changes.items():
            self.update(table, key_col, key, values)

    def rewrite(self, table: str, df: pd.DataFrame):
        from openpyxl import load_workbook
        with self._lock:
            wb = load_workbook(self.path)
            ws = wb[table]
            header = [c.value for c in ws[1]]
            ws.delete_rows(2, ws.max_row)
            for r in df.to_dict("records"):
                ws.append([_clean(r.get(c, "")) for c in header])
            wb.save(self.path)
