import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.orchestrator import AgentOrchestrator
from core.phone_utils import normalize_phone_number


def _normalize_demo_user_id(raw_value: str) -> str:
    cleaned = raw_value.strip()
    if not cleaned:
        return "default_user"

    # If the value is phone-like, normalize it to the same shape used by
    # the WhatsApp bridge so conversations can continue under one identity.
    normalized_input = cleaned.lower().replace("whatsapp:", "").strip()
    if any(char.isdigit() for char in normalized_input) and not any(char.isalpha() for char in normalized_input):
        return normalize_phone_number(cleaned, default="default_user")

    return cleaned


def main():
    print("Initializing Agent Orchestrator...")
    orchestrator = AgentOrchestrator()
    raw_user_id = input("Telefon numarasi (bos birakirsan default_user): ")
    user_id = _normalize_demo_user_id(raw_user_id)
    
    # Configure for demo
    print(f"LLM Mode: {orchestrator.config.llm_mode} (0=Ollama, 1=OpenAI)")
    if orchestrator.config.llm_mode == 0:
        print(f"Ollama Model: {orchestrator.config.ollama_model}")
        print(f"Ollama URL: {orchestrator.config.ollama_base_url}")
    print(f"Kullanici kimligi: {user_id}")
    
    print("-" * 50)
    print("Starting interactive session. Type 'exit' to quit.")
    
    while True:
        try:
            user_input = input("\nUser: ")
            if not user_input.strip():
                continue
            if user_input.lower() in ['exit', 'quit', 'q']:
                break
            
            print("Agent is thinking...")
            result = orchestrator.process_turn(user_input, user_id=user_id)
            
            print(f"\nAgent: {result['agent_response']}")
            
            # Debug info
            print(f"\n[Debug] Router Decision: {result.get('router_decision')}")
            # print(f"[Debug] Intents: {result['intents']}")
            # print(f"[Debug] Slots: {result['slots']}")
            print(f"[Debug] Handoff: {result['context']['handoff']}")
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    main()
