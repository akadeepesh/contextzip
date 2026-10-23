"""Entry point PyInstaller freezes into the standalone `contextzip` binary."""

from contextzip.cli import main

if __name__ == "__main__":
    main(prog_name="contextzip")
