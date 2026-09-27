"""Sample raising FileNotFoundError on missing file."""

from pathlib import Path


def main() -> None:
    missing_file = Path("non_existent_evaluation_data_file_987654.txt")
    _ = missing_file.read_text(encoding="utf-8")


if __name__ == "__main__":
    main()

