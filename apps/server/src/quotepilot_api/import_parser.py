"""Bounded, non-executing CSV and single-sheet OOXML readers; numbers stay text."""

import csv
import io
import posixpath
import zlib
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

from defusedxml import ElementTree as ET  # type: ignore[import-untyped]
from defusedxml.common import DefusedXmlException  # type: ignore[import-untyped]

MAX_BYTES = 2_000_000
MAX_ROWS = 2000
MAX_COLUMNS = 40
MAX_CELL = 4000
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class ImportError(ValueError):
    def __init__(self, message: str, status: int = 422) -> None:
        self.message = message
        self.status = status
        super().__init__(message)


def workbook(data: bytes) -> list[list[str]]:
    with ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(entries) > 100 or len(names) != len(set(names)):
            raise ImportError("Workbook has too many or duplicate archive entries.")
        if sum(entry.file_size for entry in entries) > 12_000_000:
            raise ImportError("Expanded workbook exceeds 12 MB.")
        for entry in entries:
            if (
                entry.flag_bits & 1
                or entry.compress_type not in {ZIP_STORED, ZIP_DEFLATED}
                or entry.file_size > 6_000_000
                or entry.file_size > max(1, entry.compress_size) * 200
                or ".." in entry.filename.split("/")
                or "vbaproject" in entry.filename.lower()
                or "externallinks" in entry.filename.lower()
            ):
                raise ImportError("Unsafe, encrypted or excessively compressed workbook.")
        # Parse every XML part with entity/DTD protection, including unused metadata.
        parts = {}
        for name in names:
            if name.endswith((".xml", ".rels")):
                root = ET.fromstring(archive.read(name), forbid_dtd=True)
                if any(e.tag == NS + "f" for e in root.iter()):
                    raise ImportError("Formulas are not supported. Export values before importing.")
                if any(e.attrib.get("TargetMode") == "External" for e in root.iter()):
                    raise ImportError("External workbook relationships are not supported.")
                parts[name] = root
        sheets = parts["xl/workbook.xml"].findall(f"{NS}sheets/{NS}sheet")
        if len(sheets) != 1:
            raise ImportError("Use a workbook with exactly one worksheet.")
        relationship = sheets[0].attrib[
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        ]
        links = parts["xl/_rels/workbook.xml.rels"]
        target = next(e.attrib["Target"] for e in links if e.attrib.get("Id") == relationship)
        path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
        shared = []
        if "xl/sharedStrings.xml" in parts:
            shared = [
                "".join(t.text or "" for t in item.iter(NS + "t"))
                for item in parts["xl/sharedStrings.xml"]
            ]
        result: list[list[str]] = []
        for row in parts[path].findall(f"{NS}sheetData/{NS}row"):
            number = int(row.attrib["r"])
            if number <= len(result) or number > MAX_ROWS + 1:
                raise ImportError("Worksheet rows must be ordered and within 2,001 rows.")
            while len(result) < number:
                result.append([])
            values = result[-1]
            for cell in row:
                if cell.tag != NS + "c":
                    continue
                ref = cell.attrib["r"]
                letters = ref.rstrip("0123456789")
                if ref[len(letters) :] != str(number):
                    raise ImportError("Cell address must match its worksheet row.")
                column = 0
                for letter in letters:
                    if not "A" <= letter <= "Z":
                        raise ImportError("Invalid worksheet cell address.")
                    column = column * 26 + ord(letter) - 64
                if not 1 <= column <= MAX_COLUMNS or column <= len(values):
                    raise ImportError("Use at most 40 ordered columns.")
                value = cell.findtext(NS + "v", "")
                kind = cell.attrib.get("t", "n")
                if kind == "s":
                    index = int(value)
                    if not 0 <= index < len(shared):
                        raise ImportError("Invalid shared string reference.")
                    value = shared[index]
                elif kind == "inlineStr":
                    value = "".join(t.text or "" for t in cell.iter(NS + "t"))
                elif kind not in {"n", "str", "d"}:
                    raise ImportError(
                        "Use text or numeric cells; errors and booleans are unsupported."
                    )
                values.extend([""] * (column - len(values) - 1))
                values.append(value)
        width = len(result[0]) if result else 0
        return [row + [""] * max(0, width - len(row)) for row in result]


def parse(filename: str, data: bytes) -> tuple[list[str], list[list[str]]]:
    if not data or len(data) > MAX_BYTES:
        raise ImportError("Choose a nonempty CSV or XLSX file of at most 2 MB.")
    try:
        if filename.lower().endswith(".csv"):
            text = data.decode("utf-8-sig")
            rows = []
            for row in csv.reader(io.StringIO(text, newline=""), strict=True):
                rows.append(row)
                if len(rows) > MAX_ROWS + 1:
                    raise ImportError("Use at most 2,000 data rows.")
        elif filename.lower().endswith(".xlsx"):
            rows = workbook(data)
        else:
            raise ImportError("Only UTF-8 CSV and single-sheet XLSX files are supported.")
    except (
        UnicodeError,
        csv.Error,
        BadZipFile,
        KeyError,
        ValueError,
        IndexError,
        StopIteration,
        ET.ParseError,
        DefusedXmlException,
        zlib.error,
    ) as error:
        if isinstance(error, ImportError):
            raise
        raise ImportError(
            "Malformed file. Check UTF-8 CSV or single-sheet XLSX structure."
        ) from None
    if len(rows) < 2 or len(rows) > MAX_ROWS + 1:
        raise ImportError("Include a header and 1–2,000 data rows.")
    headers = [value.strip() for value in rows[0]]
    if not 1 <= len(headers) <= MAX_COLUMNS or any(not h or len(h) > 100 for h in headers):
        raise ImportError("Provide 1–40 nonempty column names, each at most 100 characters.")
    if len(set(headers)) != len(headers):
        raise ImportError("Column names must be unique.")
    if any(len(row) != len(headers) for row in rows[1:]):
        raise ImportError("Every row must have the same number of columns as the header.")
    if any(len(cell) > MAX_CELL or "\x00" in cell for row in rows for cell in row):
        raise ImportError("Cells must contain at most 4,000 characters and no null bytes.")
    return headers, rows[1:]
