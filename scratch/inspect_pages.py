import sqlite3

def search_asmt():
    conn = sqlite3.connect("data/metalegal.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT title, slug, page_id FROM pages WHERE title LIKE '%ASMT%' OR full_text LIKE '%ASMT%'")
    for r in cursor.fetchall():
        print(f"ASMT Page: {r['title']} (ID: {r['page_id']}, Slug: {r['slug']})")
    conn.close()

if __name__ == '__main__':
    search_asmt()
