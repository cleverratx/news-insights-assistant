import csv
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ENCODINGS = ("utf-8-sig", "cp1251")
DELIMITERS = ";,\t|"


class CaseFileError(Exception):
    pass


@dataclass(frozen=True)
class CsvRow:
    line: int
    values: dict[str, str | None]
    width: int


@dataclass(frozen=True)
class CsvFile:
    path: Path
    encoding: str
    delimiter: str
    columns: list[str]

    def rows(self) -> Iterator[CsvRow]:
        with self.path.open(encoding=self.encoding, newline="") as handle:
            reader = csv.reader(handle, delimiter=self.delimiter)
            next(reader, None)
            for raw in reader:
                if not any(cell.strip() for cell in raw):
                    continue
                cells: list[str | None] = list(raw[: len(self.columns)])
                cells += [None] * (len(self.columns) - len(cells))
                yield CsvRow(
                    line=reader.line_num,
                    values=dict(zip(self.columns, cells, strict=True)),
                    width=len(raw),
                )


def detect_encoding(path: Path) -> str:
    content = path.read_bytes()
    for encoding in ENCODINGS:
        try:
            content.decode(encoding)
        except UnicodeDecodeError:
            continue
        return encoding
    raise CaseFileError(f"{path.name}: не удалось определить кодировку")


def open_csv(path: Path) -> CsvFile:
    if not path.is_file():
        raise CaseFileError(f"Файл не найден: {path}")
    encoding = detect_encoding(path)
    with path.open(encoding=encoding, newline="") as handle:
        header_line = handle.readline()
    if not header_line.strip():
        raise CaseFileError(f"{path.name}: пустой файл")
    counts = {delimiter: header_line.count(delimiter) for delimiter in DELIMITERS}
    delimiter = max(counts, key=lambda key: counts[key])
    header = next(csv.reader([header_line], delimiter=delimiter))
    columns = [column.strip().strip('"').upper() for column in header]
    return CsvFile(path=path, encoding=encoding, delimiter=delimiter, columns=columns)
