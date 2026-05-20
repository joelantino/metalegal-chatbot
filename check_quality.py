"""Quick quality check on crawled JSON files."""
import json
from pathlib import Path

pages_dir = Path("data/pages")
files = sorted(pages_dir.glob("*.json"))[:5]

for fpath in files:
    try:
        d = json.loads(fpath.read_text(encoding="utf-8"))
        cs = d.get("content_sections", {})
        print(f"\nURL: {d['url']}")
        print(f"  word_count : {d.get('word_count', '?')}")
        print(f"  sections   : {len(cs)}")
        print(f"  headings   : {len(d.get('headings', []))}")
        for k, v in list(cs.items())[:5]:
            print(f"    [{k[:50]}]")
            print(f"      => {v[:200]}")
    except Exception as e:
        print(f"Error reading {fpath.name}: {e}")
