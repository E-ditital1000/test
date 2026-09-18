"""
Reading a spreadsheet somebody exported, the way they actually export it.

Shared by the customer and staff imports. Both are run by Operations or HR
from a file made in Excel or Google Sheets, so this accepts what those
produce rather than what a programmer would: a byte-order mark at the start,
Windows-1252 accents, semicolons where a French or Portuguese locale puts
them, headers in any case with spaces in them, and blank lines.
"""
import csv
import io
from dataclasses import dataclass, field


class ImportFileError(Exception):
    """The file as a whole cannot be read. Nothing row-level applies."""


@dataclass
class Row:
    line: int         # as the spreadsheet numbers it: the header is line 1
    values: dict


@dataclass
class Report:
    """What an import found, row by row. Printed the same whether or not it saved."""

    errors: list = field(default_factory=list)      # (line, message) -- block the import
    warnings: list = field(default_factory=list)    # (line, message) -- worth reading
    skipped: list = field(default_factory=list)     # (line, message) -- already there
    created: list = field(default_factory=list)     # descriptions of what was (or would be) made
    committed: bool = False

    def error(self, line, message):
        self.errors.append((line, message))

    def warn(self, line, message):
        self.warnings.append((line, message))

    def skip(self, line, message):
        self.skipped.append((line, message))

    @property
    def ok(self):
        return not self.errors


def header_key(text):
    """'Contact Phone ' -> 'contact_phone'."""
    return "_".join(text.strip().lower().replace("-", " ").split())


def decode(raw):
    if isinstance(raw, str):
        return raw
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportFileError("The file is not text. Save it from the spreadsheet as CSV.")


def read_rows(raw, *, required, known):
    """
    Parse `raw` (bytes or str) into Rows keyed by normalised header.

    Raises ImportFileError when a required column is missing entirely: that
    is a wrong file, not a set of bad rows, and listing an error on every
    line would bury the one thing to fix. Unknown columns are returned so
    the caller can warn about them -- usually a typo in a header, which
    would otherwise silently drop a whole column of data.
    """
    text = decode(raw)
    if not text.strip():
        raise ImportFileError("The file is empty.")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)

    try:
        header = next(reader)
    except StopIteration:
        raise ImportFileError("The file is empty.")
    keys = [header_key(h) for h in header]

    missing = [c for c in required if c not in keys]
    if missing:
        raise ImportFileError(
            "Missing column{}: {}. The first line must name the columns.".format(
                "s" if len(missing) > 1 else "", ", ".join(missing)
            )
        )
    unknown = [h for h, k in zip(header, keys) if k and k not in known]

    rows = []
    start = reader.line_num + 1
    for cells in reader:
        # The line the record starts on, as the spreadsheet shows it. Not a
        # count of records: an address with a line break in its cell spans
        # two lines, and every later number would be off by one.
        line, start = start, reader.line_num + 1
        if not any(cell.strip() for cell in cells):
            continue
        values = {}
        for key, cell in zip(keys, cells):
            if key in known:
                values[key] = " ".join(cell.split())
        rows.append(Row(line=line, values=values))
    return rows, unknown


def print_report(report, write, *, noun, plural=None):
    """The same plain report for a dry run and for a real one."""
    for title, entries in (("Errors", report.errors), ("Warnings", report.warnings), ("Skipped", report.skipped)):
        if entries:
            write(f"\n{title} ({len(entries)}):")
            for line, message in entries:
                write(f"  line {line}: {message}" if line else f"  {message}")
    write("")
    verb = "Created" if report.committed else "Would create"
    count = len(report.created)
    write(f"{verb} {count} {noun if count == 1 else (plural or noun + 's')}.")
    for description in report.created:
        write(f"  {description}")
