from __future__ import annotations

import argparse
from pathlib import Path

from recoup_agent.extraction.eval.long_contract import build_long_contract


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the deterministic long-contract extraction fixture")
    parser.add_argument("--output", type=Path, default=Path("long-contract.txt"))
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    text, expected = build_long_contract(args.seed)
    args.output.write_text(text)
    print(f"wrote {args.output} ({len(text)} characters; {len(expected)} planted terms)")


if __name__ == "__main__":
    main()
