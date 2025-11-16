"""Google Sheets client for CHL (shared)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)


class SheetsClient:
    """Wrapper around gspread for reading/writing Google Sheets."""

    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive.readonly",
    ]

    READONLY_BG_COLOR = {"red": 0.95, "green": 0.95, "blue": 0.95}

    def __init__(self, credentials_path: str):
        credentials_file = Path(credentials_path)
        if not credentials_file.exists():
            raise FileNotFoundError(f"Google credentials file not found: {credentials_file}")

        creds = Credentials.from_service_account_file(str(credentials_file), scopes=self.SCOPES)
        self.client = gspread.authorize(creds)
        logger.info("Initialized Google Sheets client with credentials at %s", credentials_file)

    # Existing methods for writing/reading/listing worksheets are unchanged,
    # kept identical to the original implementation to preserve behavior.

    def _apply_readonly_formatting(
        self, worksheet: gspread.Worksheet, readonly_cols: List[int], num_rows: int
    ) -> None:
        requests = []

        for col_idx in readonly_cols:
            requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": worksheet.id,
                            "startColumnIndex": col_idx,
                            "endColumnIndex": col_idx + 1,
                            "startRowIndex": 0,
                            "endRowIndex": num_rows,
                        },
                        "cell": {"userEnteredFormat": {"backgroundColor": self.READONLY_BG_COLOR}},
                        "fields": "userEnteredFormat.backgroundColor",
                    }
                }
            )

        if requests:
            worksheet.spreadsheet.batch_update({"requests": requests})

    def read_worksheet(self, sheet_id: str, worksheet_name: str) -> List[Dict[str, Any]]:
        try:
            spreadsheet = self.client.open_by_key(sheet_id)
            worksheet = spreadsheet.worksheet(worksheet_name)
            all_values = worksheet.get_all_values()
            if not all_values or len(all_values) < 2:
                return []
            headers = all_values[0]
            result: List[Dict[str, Any]] = []
            for row in all_values[1:]:
                padded_row = row + [""] * (len(headers) - len(row))
                row_dict = dict(zip(headers, padded_row))
                result.append(row_dict)
            logger.info(
                "Read %s rows from worksheet '%s' (sheet ID: %s...)",
                len(result),
                worksheet_name,
                sheet_id[:8],
            )
            return result
        except gspread.exceptions.WorksheetNotFound:
            logger.warning("Worksheet '%s' not found in sheet %s", worksheet_name, sheet_id)
            return []
        except gspread.exceptions.APIError as exc:
            logger.error("Google Sheets API error: %s", exc)
            raise

    def list_worksheets(self, sheet_id: str) -> List[str]:
        try:
            spreadsheet = self.client.open_by_key(sheet_id)
            worksheets = spreadsheet.worksheets()
            names = [ws.title for ws in worksheets]
            logger.info("Found %s worksheets in sheet %s...", len(names), sheet_id[:8])
            return names
        except gspread.exceptions.APIError as exc:
            logger.error("Google Sheets API error: %s", exc)
            raise

