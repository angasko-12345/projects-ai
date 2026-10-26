import sys


def _run() -> int:
    argv = sys.argv[1:]
    from .cli import COMMANDS

    if argv and argv[0] in COMMANDS:
        # headless path: never imports PySide6
        from .cli import main as cli_main

        return cli_main(argv)
    from .gui import main

    return main()


if __name__ == "__main__":
    sys.exit(_run())
