import sqlite3

def explore():
    conn = sqlite3.connect("data/metalegal.db")
    cursor = conn.cursor()
    
    # Let's see some actual pages and their titles
    print("\n--- Distinct Page Titles ---")
    cursor.execute("SELECT url, title, length(full_text) FROM pages WHERE title IS NOT NULL AND title != '' LIMIT 30")
    for row in cursor.fetchall():
        print(f"URL: {row[0]} | Title: {row[1]} | Text Len: {row[2]}")
        
    print("\n--- Sample FAQ questions from faq_index ---")
    cursor.execute("SELECT question, url_index.url FROM faq_index JOIN url_index ON faq_index.page_id = url_index.page_id LIMIT 20")
    for row in cursor.fetchall():
        print(f"FAQ Q: {row[0]} | URL: {row[1]}")
        
    print("\n--- Sample Intents from intent_index ---")
    cursor.execute("SELECT intent, url FROM intent_index LIMIT 20")
    for row in cursor.fetchall():
        print(f"Intent: {row[0]} | URL: {row[1]}")
        
    conn.close()

if __name__ == "__main__":
    explore()
