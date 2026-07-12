import argparse
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.orchestrator import AgentOrchestrator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Whisper Large + ElevenLabs ile tek turluk sesli agent demosu.",
    )
    parser.add_argument("audio_path", help="Transcribe edilecek yerel ses dosyasi")
    parser.add_argument(
        "--user-id",
        default="default_user",
        help="Konusma kimligi",
    )
    parser.add_argument(
        "--channel",
        default="voice",
        help="Session channel etiketi",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Uretilecek ses cevabinin hedef dosyasi",
    )
    parser.add_argument(
        "--no-tts",
        action="store_true",
        help="Sadece Whisper ile yaziya cevir, ElevenLabs sesi uretme",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    orchestrator = AgentOrchestrator()
    result = orchestrator.process_audio_turn(
        audio_path=args.audio_path,
        user_id=args.user_id,
        channel=args.channel,
        output_audio_path=args.output or None,
        synthesize_reply=not args.no_tts,
    )

    print(f"Transcript: {result['user_input']}")
    print(f"Agent: {result['agent_response']}")
    if result["reply_audio_path"]:
        print(f"Reply Audio: {result['reply_audio_path']}")


if __name__ == "__main__":
    main()
