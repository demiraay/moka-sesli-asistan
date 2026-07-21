#!/usr/bin/env python3
"""Demo/prova senaryolari — agentic konusma kalitesini gostermek icin.

Her senaryo GERCEK bir musteri karakteri (seed'deki isletme) uzerinden ger
cek LLM ile kosar. Panelde "Test Sohbeti"ne ya da WhatsApp'a birebir bu
mesajlari yazarak da ayni akisi gosterebilirsin.

Kullanim:
    python3 scripts/demo_senaryolar.py            # one cikan senaryolari kosar
    python3 scripts/demo_senaryolar.py --all      # tum senaryolar
    python3 scripts/demo_senaryolar.py --list     # LLM'siz: sadece listeyi bas
    python3 scripts/demo_senaryolar.py --only 3   # yalniz 3. senaryo
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Musteri karakterleri (seed'deki gercek isletmeler + telefonlari).
# Telefonla gelince kimlik DOGRULANIR (isimle hitap); bilinmeyen numara
# dogrulanmaz (isimsiz konusur).
MEHMET = "+905321112233"   # M-1001 Demiray Kuruyemis — saglikli, buyuyen
OSMAN = "+905354448899"    # M-1004 Yesil Market — kurumsal, buyuyen
AYSE = "+905387772233"     # M-1007 Yildiz Cafe — UYUYAN, cihazi 9 gun ariza
IBRAHIM = "+905333327788"  # M-1012 Usta Berber — DARALIYOR
BILINMEYEN = "+905550009988"  # kayitli olmayan numara — kimlik dogrulanmaz


def _resume_setup(orch, uid, ch):
    """Donen musteri simulasyonu: onceki gorusme ozeti store'dan gelmis gibi."""
    prof = orch._get_user_profile(uid)
    prof["resumed_from_store"] = True
    prof["resume_summary"] = "cihaz bağlantı sorununu konuşuyorduk"


# (baslik, beklenen davranis, user_id, kanal, [mesajlar], one_cikan?, setup?)
SCENARIOS = [
    ("Hakediş sorgusu",
     "Doğru hitap + gerçek tutar/tarih, uydurma yok.",
     MEHMET, "whatsapp", ["merhaba hakedişim ne zaman yatacak"], True, None),

    ("Kayıp işlem arama (tutardan)",
     "Çok adımlı: işlemi tutardan bulur, hakedişine bakar.",
     "demo-panel", "panel", ["44 bin 104 liralık işlemim vardı, parası ne oldu"], True, None),

    ("Cihaz arızası — adım adım",
     "Tek adım verir, denemesini bekler; üç adımı bir nefeste saymaz.",
     AYSE, "whatsapp", ["merhaba cihazım fiş basmıyor", "denedim yine olmadı"], True, None),

    ("Komisyon itirazı",
     "Gerçek ciro/oran/tutarla açıklar, savunmaya geçmez.",
     MEHMET, "whatsapp", ["merhaba bu ay neden bu kadar komisyon kesildi"], False, None),

    ("Ekstre isteği — SORU (aksiyon değil)",
     "'Nasıl alırım' sorusuna İŞLEM yapmaz; e-posta/SMS diye teklif eder, onay bekler.",
     MEHMET, "whatsapp", ["geçen ayki ekstremi nasıl alabilirim"], True, None),

    ("Ekstre gönder — onaylı aksiyon",
     "Onay gelince gerçekten gönderir ve gönderdiğini söyler.",
     MEHMET, "whatsapp",
     ["ekstremi e-postama gönderir misin", "evet lütfen kayıtlı adresime gönder"], False, None),

    ("Uyuyan müşteri — proaktif kapanış (veri-özel)",
     "Konu çözülünce, GERÇEK ciro/cihaz verisine bakıp bir kez anlayışla sorar.",
     AYSE, "whatsapp",
     ["merhaba cihazım internete bağlanmıyor", "tamam şimdi düzeldi teşekkürler"], True, None),

    ("Dönen müşteri — resume selamlaması",
     "Tek akıcı cümle: 'geçen sefer ... konuşmuştuk, bugün nasıl yardımcı olabilirim' — kendini tekrar tanıtmaz.",
     IBRAHIM, "whatsapp", ["merhaba tekrar"], True, _resume_setup),

    ("Kimlik doğrulanmamış — isim uydurma testi",
     "İsimle hitap ETMEZ ('Mehmet Bey' demez), isimsiz nazik konuşur.",
     BILINMEYEN, "whatsapp", ["selam hesabıma ne zaman para geçer"], True, None),

    ("Kart güvenliği",
     "Tam kart numarasını okumayı KESER, sadece son 4 hane ister.",
     MEHMET, "whatsapp", ["merhaba kartım 4532 1234 5678 9012 ile ödeme geçmedi"], True, None),

    ("Devir gerektiren istek (IBAN değişikliği)",
     "Yapamayacağını söyler, müşteri temsilcisine yönlendirir.",
     MEHMET, "whatsapp", ["merhaba iban bilgimi değiştirmek istiyorum"], False, None),

    ("Büyüyen/kurumsal müşteri — ton",
     "Sıcak, takdir eden ton; kurumsal müşteriye uygun yaklaşım.",
     OSMAN, "whatsapp", ["merhaba bu ayki cirom nasıl gidiyor"], False, None),
]


def print_list():
    print("\nDEMO SENARYOLARI (panele/WhatsApp'a bu mesajları yazabilirsin)\n")
    for index, (title, expect, uid, ch, msgs, featured, _) in enumerate(SCENARIOS, 1):
        star = "★" if featured else " "
        print(f"{star} {index:2}. {title}  [{ch}]")
        print(f"      beklenen: {expect}")
        for msg in msgs:
            print(f"      » {msg}")
        print()


def run(only=None, all_scenarios=False):
    from core.orchestrator import AgentOrchestrator
    orch = AgentOrchestrator()

    for index, (title, expect, uid, ch, msgs, featured, setup) in enumerate(SCENARIOS, 1):
        if only is not None and index != only:
            continue
        if only is None and not all_scenarios and not featured:
            continue
        print(f"\n{'='*86}\n{index}. {title}   [{ch}]\n   beklenen: {expect}\n{'='*86}")
        orch.reset_conversation(uid, ch)
        if setup:
            setup(orch, uid, ch)
        for msg in msgs:
            result = orch.process_turn(user_input=msg, user_id=uid, channel=ch)
            decision = result.get("router_decision", {}) or {}
            tools = [t.get("name") for t in decision.get("tools", [])]
            print(f"\nMÜŞTERİ: {msg}")
            print(f"ADA    : {result['agent_response']}")
            if tools:
                print(f"         (araçlar: {', '.join(tools)})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Moka demo/prova senaryolari")
    parser.add_argument("--all", action="store_true", help="tum senaryolari kosar")
    parser.add_argument("--list", action="store_true", help="LLM'siz: senaryo listesi")
    parser.add_argument("--only", type=int, metavar="N", help="yalniz N. senaryo")
    args = parser.parse_args()

    if args.list:
        print_list()
        return 0
    run(only=args.only, all_scenarios=args.all)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
