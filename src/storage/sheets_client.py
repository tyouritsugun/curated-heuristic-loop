"""Google Sheets client used by export/import operations."""
import logging
from typing import Any, Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)


class SheetsClient:
    """Google Sheets client using gspread library with service account auth."""

    # Google Sheets API scopes
    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    # Visual conventions for read-only fields
    READONLY_BG_COLOR = {"red": 0.95, "green": 0.95, "blue": 0.95}  # Light gray
    EDITABLE_BG_COLOR = {"red": 1.0, "green": 1.0, "blue": 1.0}  # White

    def __init__(self, credentials_path: str):
        """Initialize with service account credentials.

        Args:
            credentials_path: Path to service account JSON credentials

        Raises:
            FileNotFoundError: If credentials file not found
            ValueError: If credentials are invalid
        """
        self.credentials_path = credentials_path
        self.client = self._get_authenticated_client()

    def _get_authenticated_client(self) -> gspread.Client:
        """Create authenticated Google Sheets client.

        Returns:
            Authenticated gspread client

        Note:
            Uses google-auth library (oauth2client is deprecated since 2017)
        """
        try:
            creds = Credentials.from_service_account_file(
                self.credentials_path, scopes=self.SCOPES
            )
            return gspread.authorize(creds)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Google credentials file not found: {self.credentials_path}"
            )
        except Exception as e:
            raise ValueError(f"Failed to authenticate with Google Sheets: {e}") from e

    def create_or_update_worksheet(
        self,
        sheet_id: str,
        worksheet_name: str,
        data: List[List[Any]],
        read_only_columns: Optional[List[int]] = None,
    ) -> None:
        """Create or update worksheet with data and background colors.

        Args:
            sheet_id: Google Sheet ID
            worksheet_name: Worksheet/tab name
            data: 2D list of cell values (rows x columns), including header row
            read_only_columns: List of 0-indexed column indices to mark with gray background

        Raises:
            gspread.exceptions.APIError: If API call fails
        """
        if not data:
            logger.warning(f"No data to write to worksheet '{worksheet_name}'")
            return

        try:
            # Open spreadsheet
            spreadsheet = self.client.open_by_key(sheet_id)

            # Try to get existing worksheet, create if doesn't exist
            try:
                worksheet = spreadsheet.worksheet(worksheet_name)
                # Clear existing content
                worksheet.clear()
            except gspread.exceptions.WorksheetNotFound:
                # Create new worksheet
                worksheet = spreadsheet.add_worksheet(
                    title=worksheet_name,
                    rows=len(data) + 100,  # Extra rows for future entries
                    cols=len(data[0]) if data else 10,
                )

            # Write data
            worksheet.update(data, "A1")

            # Apply background colors for read-only columns
            if read_only_columns:
                self._apply_readonly_formatting(worksheet, read_only_columns, len(data))

            logger.info(
                f"Successfully updated worksheet '{worksheet_name}' "
                f"with {len(data)} rows, {len(data[0])} columns"
            )

        except gspread.exceptions.APIError as e:
            logger.error(f"Google Sheets API error: {e}")
            raise

    def _apply_readonly_formatting(
        self, worksheet: gspread.Worksheet, readonly_cols: List[int], num_rows: int
    ) -> None:
        """Apply gray background to read-only columns.

        Args:
            worksheet: Worksheet to format
            readonly_cols: List of 0-indexed column indices
            num_rows: Number of rows to format
        """
        requests = []

        for col_idx in readonly_cols:
            # Format entire column (all rows including header)
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
        """Read worksheet data as list of dictionaries.

        Args:
            sheet_id: Google Sheet ID
            worksheet_name: Worksheet/tab name

        Returns:
            List of rows as dictionaries (column name -> value)
            Empty list if worksheet not found or has no data

        Raises:
            gspread.exceptions.APIError: If API call fails
        """
        try:
            spreadsheet = self.client.open_by_key(sheet_id)
            worksheet = spreadsheet.worksheet(worksheet_name)

            # Get all values as list of lists
            all_values = worksheet.get_all_values()

            if not all_values or len(all_values) < 2:
                # No data (only header or empty)
                return []

            # First row is header
            headers = all_values[0]

            # Convert to list of dictionaries
            result = []
            for row in all_values[1:]:
                # Pad row with empty strings if shorter than headers
                padded_row = row + [""] * (len(headers) - len(row))
                row_dict = dict(zip(headers, padded_row))
                result.append(row_dict)

            logger.info(
                f"Read {len(result)} rows from worksheet '{worksheet_name}' "
                f"(sheet ID: {sheet_id[:8]}...)"
            )
            return result

        except gspread.exceptions.WorksheetNotFound:
            logger.warning(f"Worksheet '{worksheet_name}' not found in sheet {sheet_id}")
            return []
        except gspread.exceptions.APIError as e:
            logger.error(f"Google Sheets API error: {e}")
            raise

    def list_worksheets(self, sheet_id: str) -> List[str]:
        """List all worksheet names in a spreadsheet.

        Args:
            sheet_id: Google Sheet ID

        Returns:
            List of worksheet names

        Raises:
            gspread.exceptions.APIError: If API call fails
        """
        try:
            spreadsheet = self.client.open_by_key(sheet_id)
            worksheets = spreadsheet.worksheets()
            names = [ws.title for ws in worksheets]
            logger.info(f"Found {len(names)} worksheets in sheet {sheet_id[:8]}...")
            return names
        except gspread.exceptions.APIError as e:
            logger.error(f"Google Sheets API error: {e}")
            raise

    def delete_worksheet(self, sheet_id: str, worksheet_name: str) -> None:
        """Delete a worksheet from spreadsheet.

        Args:
            sheet_id: Google Sheet ID
            worksheet_name: Worksheet/tab name

        Raises:
            gspread.exceptions.WorksheetNotFound: If worksheet doesn't exist
            gspread.exceptions.APIError: If API call fails
        """
        try:
            spreadsheet = self.client.open_by_key(sheet_id)
            worksheet = spreadsheet.worksheet(worksheet_name)
            spreadsheet.del_worksheet(worksheet)
            logger.info(f"Deleted worksheet '{worksheet_name}'")
        except gspread.exceptions.WorksheetNotFound:
            logger.warning(f"Worksheet '{worksheet_name}' not found, skipping delete")
        except gspread.exceptions.APIError as e:
            logger.error(f"Google Sheets API error: {e}")
            raise
