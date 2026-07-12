import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from whatsapp import create_app


def main() -> None:
    app = create_app()
    host = os.getenv("WHATSAPP_BRIDGE_HOST", "127.0.0.1")
    port = int(os.getenv("WHATSAPP_BRIDGE_PORT", "5051"))
    debug = os.getenv("WHATSAPP_BRIDGE_DEBUG", "false").lower() in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug, use_reloader=debug)


if __name__ == "__main__":
    main()
