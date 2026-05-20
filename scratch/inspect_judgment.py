import sqlite3

def inspect_judgment():
    conn = sqlite3.connect("data/metalegal.db")
    conn.row_factory = sqlite3.Row
    print("Searching chunks table for 'Viraj Shah':")
    rows = conn.execute("SELECT chunk_id, page_id, section, text FROM chunks WHERE text LIKE '%Viraj Shah%'").fetchall()
    print(f"Found {len(rows)} matching chunks:")
    for r in rows:
        print(f"\nChunk ID: {r['chunk_id']} | Page ID: {r['page_id']} | Section: {r['section']}")
        print(f"Text Content:\n{r['text']}")
    conn.close()

if __name__ == '__main__':
    inspect_judgment()
