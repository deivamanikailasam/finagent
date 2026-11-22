"""Print a section hash: uv run python scripts/section_hash.py <file> <n>"""

import sys
from pathlib import Path

from finagent.rag.section_hash import section_hash

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: section_hash.py <file> <n>")
    print(section_hash(Path(sys.argv[1]).read_text(encoding="utf-8"), int(sys.argv[2])))
