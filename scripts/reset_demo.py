"""Demo sifirlama: konusma/lead/gorev veritabanini ve uretilen ses dosyalarini temizler.

Kullanim (panel KAPALIYKEN calistirin):
    .venv/bin/python scripts/reset_demo.py            # her seyi sil
    .venv/bin/python scripts/reset_demo.py --seed     # sil + panele hafif ornek veri koy

--seed, juri onunde bos gorunmemesi icin panele birkac gecmis cagri/gorev yazar;
demo isletme M-1001/M-1007'yi KIRLETMEZ.
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "admin.sqlite3"
VOICE_DIR = BASE_DIR / "voice_output"


def main() -> int:
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"silindi: {DB_PATH}")
    else:
        print("veritabani zaten yok")

    removed = 0
    if VOICE_DIR.exists():
        for f in VOICE_DIR.glob("*"):
            if f.is_file():
                f.unlink()
                removed += 1
    print(f"ses dosyasi temizlendi: {removed}")

    if "--seed" in sys.argv:
        from core.admin_store import AdminStore

        store = AdminStore()
        # Panel bos gorunmesin: birkac tarihsel cagri + acik gorev
        seed_calls = [
            ("seed-hasan", "Hasan Kara — Lezzet Lokantası", "Hakediş sorgusu çözüldü.",
             "Geçen haftaki hakedişlerim yattı mı?", "Evet Hasan Bey, üç hakediş de ödendi."),
            ("seed-selin", "Selin Aydın — Nova Butik", "Sanal POS 3D sorusu yanıtlandı.",
             "Sitede 3D doğrulama hatası alıyorum.", "SMS gecikmesi görünüyor; işlem kayıtlarından kontrol ettim."),
            ("seed-osman", "Osman Yeşil — Yeşil Market", "Ekstre gönderildi.",
             "Haziran ekstremi gönderir misiniz?", "Ekstreniz kayıtlı e-posta adresinize gönderildi Osman Bey."),
        ]
        import uuid
        for user_id, name, summary, q, a in seed_calls:
            store.log_turn(
                session_id=str(uuid.uuid4()), user_id=user_id, channel="voice",
                user_input=q, agent_response=a,
                router_decision={"tool": "answer_general", "args": {}},
                context={"message_facts": [], "handoff": {"required": False}},
            )
            store.save_user_ai_notes(user_id=user_id, ai_summary=summary,
                                     ai_notes={"name": name})
        store.create_task(title="Servis raporu: geçen haftanın cihaz değişimleri", user_id="")
        print("ornek veri yazildi (3 cagri + 1 gorev)")

    print("hazir. Paneli baslatabilirsiniz: .venv/bin/python server.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
