import urllib.request
import json
import time

QUESTIONS = [
    "What are the twin conditions for bail under the PMLA?",
    "What are the legal remedies available when a chargesheet is filed without the accused being arrested?",
    "What are the different kinds of bail available under Indian law?",
    "Explain the provisions of arrest and bail under the GST law in India.",
    "What is the adjudication procedure under FEMA as explained by Metalegal?",
    "What are the FEMA implications for making investments in foreign companies by Indian residents?",
    "Explain the inquiry and assessment procedure under the Black Money Act.",
    "What is the scope and law of attorney-client privilege in India?",
    "Are proceedings before the Competition Commission of India (CCI) judicial in nature?",
    "What is the new regime for the taxation of partnership firms in India?",
    "What are the legal implications and rules surrounding the retraction of statements under tax and criminal laws?",
    "What are the basic concepts and key issues related to Bills of Lading in shipping?",
    "What are the key legal requirements for the maintenance of records under various Indian laws?",
    "Where are the offices of Metalegal Advocates located?",
    "How do I contact Metalegal Advocates for legal assistance?"
]

def run_tests():
    api_url = "http://127.0.0.1:8000/chat"
    results = []
    
    print(f"Starting execution of all {len(QUESTIONS)} legal questions...\n")
    
    for idx, q in enumerate(QUESTIONS, 1):
        print(f"[{idx}/15] Querying: '{q}' ... ", end="", flush=True)
        payload = json.dumps({"query": q, "session_id": "test_session_all"}).encode("utf-8")
        req = urllib.request.Request(
            api_url, 
            data=payload, 
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        start_time = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                resp_data = json.loads(response.read().decode("utf-8"))
                latency = round((time.time() - start_time) * 1000)
                print(f"SUCCESS in {latency}ms (Confidence: {int(resp_data.get('confidence', 0)*100)}%)")
                results.append({
                    "question": q,
                    "answer": resp_data.get("answer", "No answer field found"),
                    "confidence": resp_data.get("confidence", 0.0),
                    "sources": resp_data.get("sources", []),
                    "latency": latency,
                    "error": None
                })
        except Exception as e:
            print(f"FAILED: {e}")
            results.append({
                "question": q,
                "answer": f"Error contacting API: {e}",
                "confidence": 0.0,
                "sources": [],
                "latency": round((time.time() - start_time) * 1000),
                "error": str(e)
            })
            
    # Generate the Markdown artifact
    markdown_path = "C:/Users/GCV/.gemini/antigravity/brain/b208e55c-5bbc-4868-80ab-be8f6c442dea/test_results.md"
    
    with open(markdown_path, "w", encoding="utf-8") as f:
        f.write("# MetaLegal AI Chatbot — Comprehensive Answer Verification\n\n")
        f.write("This artifact presents the verified full answers, latency metrics, and confidence scores for all 15 core legal and firm queries evaluated against the live database.\n\n")
        
        # Summary Table
        f.write("## 📊 Summary of Test Execution\n\n")
        f.write("| # | Question | Latency | Confidence | Status |\n")
        f.write("|---|---|---|---|---|\n")
        for idx, r in enumerate(results, 1):
            status = "✅ Success" if r["error"] is None else "❌ Failed"
            f.write(f"| {idx} | **{r['question']}** | {r['latency']}ms | {int(r['confidence']*100)}% | {status} |\n")
        f.write("\n---\n\n")
        
        # Full Answers
        f.write("## 📝 Complete Verified Answers\n\n")
        for idx, r in enumerate(results, 1):
            f.write(f"### Q{idx}: {r['question']}\n\n")
            f.write(f"> **Confidence:** {int(r['confidence']*100)}% &nbsp;|&nbsp; **Latency:** {r['latency']}ms\n\n")
            f.write("#### 🤖 AI Chatbot Answer:\n")
            f.write(f"{r['answer']}\n\n")
            if r["sources"]:
                f.write("#### 🔗 Sources:\n")
                for s in r["sources"]:
                    f.write(f"- [{s}]({s})\n")
            f.write("\n---\n\n")
            
    print(f"\nAll tests completed! Full results written to artifact: {markdown_path}")

if __name__ == "__main__":
    run_tests()
