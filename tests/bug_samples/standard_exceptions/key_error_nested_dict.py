"""Sample raising KeyError on missing dictionary key."""


def main() -> None:
    config: dict[str, dict[str, str]] = {"server": {"host": "127.0.0.1"}}
    _ = config["server"]["port"]


if __name__ == "__main__":
    main()

