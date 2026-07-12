import sys
import os
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.orchestrator import AgentOrchestrator

def run_exam():
    orch = AgentOrchestrator()
    
    questions = [
        # --- Greetings & General ---
        "1. Merhaba kolay gelsin.",
        "2. Proje tam olarak nerede, hangi semtte?",
        "3. Sitede havuz veya spor salonu var mı?",
        
        # --- Availability (Basic) ---
        "4. Elinizde şu an boş daire var mı?",
        "5. 3+1 daireleriniz var mı?",
        "6. A blokta boş yer kaldı mı?",
        
        # --- Advanced Filtering ---
        "7. 2. katta daire arıyorum.",
        "8. Güneş alan, aydınlık bir ev istiyorum.",
        "9. Kuzey cephe olmayan daireler hangileri?",
        
        # --- Price & Budget ---
        "10. Genel olarak fiyat aralığınız nedir?",
        "11. En ucuz daire hangisi?",
        "12. En pahalı dairenin fiyatı ne kadar?",
        "13. 10 Milyon TL altındaki daireleri listele.",
        "14. Bütçem 20 Milyon üzeri, lüks ne var?",
        "15. INV-0003 kodlu dairenin fiyatı nedir?",
        
        # --- Campaigns ---
        "16. Şu an aktif bir indirim veya kampanya var mı?",
        "17. Peşin ödemede indirim yapıyor musunuz?",

        # --- Handoff & Closing ---
        "18. Evi gelip yerinde görmek istiyorum.",
        "19. Satın almaya karar verdim, kaporayı nasıl yatırabilirim?",
        
        # --- Out of Scope ---
        "20. Trabzon'da bildiğiniz başka proje var mı?"
    ]
    
    print(f"--- STARTING 20-QUESTION EXAM ---\n")
    print(f"Model: {orch.config.ollama_model}")
    print("-" * 60)

    # Run only first 5 questions for quick verification
    for i, q_text in enumerate(questions[:20]):
        # Strip number prefix for actual processing
        clean_q = q_text.split(". ", 1)[1] if ". " in q_text else q_text
        
        print(f"\n❔ QUESTION {i+1}: {clean_q}")
        print("   thinking...", end="", flush=True)
        start_time = time.time()
        
        try:
            # Process Turn
            result = orch.process_turn(clean_q)
            print("\r", end="") # Clear thinking line
            duration = time.time() - start_time
            
            # Extract info
            router = result.get('router_decision', {})
            tool = router.get('tool', 'UNKNOWN')
            args = router.get('args', {})
            response = result.get('agent_response', '').strip()
            
            # Summarize Context
            ctx_summary = "context_empty"
            if result.get('context'):
                units = result['context'].get('units', [])
                if units:
                    ctx_summary = f"Units Found: {len(units)} (Visualized)"
                elif result['context'].get('handoff', {}).get('required'):
                    ctx_summary = f"Handoff Reason: {result['context']['handoff']['reason']}"
                elif result['context'].get('message_facts'):
                    ctx_summary = f"Facts: {result['context']['message_facts']}"
            
            print(f"🧠 ROUTER: {tool} | Args: {args}")
            print(f"📊 CONTEXT: {ctx_summary}")
            print(f"🤖 AGENT: {response}")
            print(f"⏱️ Time: {duration:.2f}s")
            
        except Exception as e:
            print(f"❌ ERROR: {e}")
        
        print("-" * 60)

if __name__ == "__main__":
    run_exam()
