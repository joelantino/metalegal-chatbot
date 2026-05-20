"""Watch crawl progress and print final summary when done."""
import time
from pathlib import Path

pages_dir = Path("data/pages")
log_path  = Path("crawl.log")
last_count = -1

print("Watching crawl... checking every 60 seconds.")
print("-" * 50)

while True:
    files = list(pages_dir.glob("*.json"))
    count = len(files)
    ts = time.strftime("%H:%M:%S")

    if count != last_count:
        print(f"[{ts}] Pages saved so far: {count}")
        last_count = count

    try:
        log_text = log_path.read_text(encoding="utf-8", errors="ignore")
        if "CRAWL COMPLETE" in log_text:
            print()
            print("=" * 60)
            print("  CRAWL COMPLETE!")
            print("=" * 60)
            lines = log_text.splitlines()
            # Print last 25 lines of log
            for line in lines[-25:]:
                if line.strip():
                    print(line)
            print("=" * 60)
            print(f"Total JSON files in data/pages: {count}")
            break
    except Exception as e:
        print(f"[{ts}] Log read error: {e}")

    time.sleep(60)
