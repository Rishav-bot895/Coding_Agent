"""Clean sample: CSV row parser and record generator."""


def parse_csv_lines(header: str, rows: list[str]) -> list[dict[str, str]]:
    """Parse CSV header and data rows into list of dictionaries."""
    keys = [k.strip() for k in header.split(",")]
    records: list[dict[str, str]] = []
    for row in rows:
        values = [v.strip() for v in row.split(",")]
        records.append(dict(zip(keys, values, strict=True)))
    return records


def main() -> None:
    hdr = "id,name,role"
    lines = ["1,Alice,Admin", "2,Bob,Engineer"]
    parsed = parse_csv_lines(hdr, lines)
    assert len(parsed) == 2
    assert parsed[0]["name"] == "Alice"
    assert parsed[1]["role"] == "Engineer"


if __name__ == "__main__":
    main()

