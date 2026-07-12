# Moka Sesli Asistan (Ada) — AI Destekli Sesli Müşteri Hattı

**Moka United FinTech Hackathon: Hack the Idea** projesi.

Bankaların "1'e basın, 2'ye basın, sırada 14. kişisiniz" çilesinin yerine geçen,
işletme müşterileriyle **doğal Türkçe sesle** konuşan tam agentic bir yapay zeka
destek hattı. Ada, arayan esnafın derdini **tek çağrıda gerçek veriyle** çözer —
ve destek hattını maliyet merkezi olmaktan çıkarıp **gelir motoruna** çevirir:

- **Anında çözüm**: hakediş ("param ne zaman yatacak?"), işlem sorgusu
  ("dün 1.250 TL çektim, göremiyorum"), POS arızası (adım adım + servis kaydı),
  komisyon açıklama, ekstre gönderimi — hepsi gerçek işlem verisine dayanır,
  asla uydurmaz.
- **Her çağrıda satış fırsatı**: POS'u bozulan esnafa anında ödeme linki, cirosu
  büyüyene daha uygun komisyon planı, Instagram'dan satana sanal POS önerisi.
- **Proaktif kurtarma (outbound)**: işlem hacmi düşen "uyuyan" işletmeleri panel
  tespit eder; tek tıkla **AI kendisi arar**, churn sebebini öğrenir, sadakat
  teklifi sunar. Kabul edilen her teklif panele **"Kurtarılan Hacim ₺"** olarak işlenir.
- **Akıllı insan devri**: öfke/fraud/hukuki sinyalde tüm bağlam özetiyle
  temsilciye aktarır (panelde SLA sayaçlı handoff kuyruğu).

## Mimari

```
Arayan işletme ──> Tarayıcı "arama" ekranı (/call)
                    · VAD: konuşma otomatik algılanır (tuş yok)
                    · barge-in: Ada konuşurken söze girilebilir
                         │  webm/opus
                         ▼
             Flask call API (:5050/call/*)
                         │
     STT: Groq Whisper (large-v3-turbo, ~0.4s)  [lokal whisper fallback]
                         ▼
             AgentOrchestrator (core/)
              ├── Router LLM (gpt-oss-20b): araç seçimi + müşteri kartı (JSON)
              ├── 9 araç → mock Moka backend (data/*.json)
              │    hakediş · işlem · POS arıza · komisyon · ekstre
              │    ödeme linki · teklif · handoff · genel
              └── Cevap LLM (gpt-oss-120b): doğal Türkçe, sese uygun
                         ▼
     TTS: ElevenLabs flash v2.5 (~0.6s)  →  tarayıcıda otomatik çalar

Yönetim paneli :5050/admin  ←→  SQLite + JSON mock backend
WhatsApp (ikincil kanal): Node bot → köprü :5051 → aynı orchestrator
```

**Uçtan uca tur süresi ~2-3 saniye** (STT+LLM+LLM+TTS). Kimlik telefondan gelir
(CTI gibi): AI kim aradığını bilir, asla "TC kimlik no'nuzu tuşlayın" demez.

## Kurulum

```bash
python3 -m venv .venv
.venv/bin/pip install flask python-dotenv pytest    # çekirdek
# opsiyonel: lokal STT fallback için → .venv/bin/pip install openai-whisper (ffmpeg gerekir)

cp .env.example .env    # ve doldurun:
#   GROQ_API_KEY        → console.groq.com (ücretsiz) — LLM + STT
#   ELEVENLABS_API_KEY  → elevenlabs.io — Ada'nın Türkçe sesi
#   ELEVENLABS_VOICE_ID → bir premade ses (ör. Sarah: EXAVITQu4vr4xnSDxMaL)
```

## Çalıştırma

```bash
.venv/bin/python server.py         # panel + köprü (+ Node kuruluysa WhatsApp botu)
# veya yalnızca panel:
.venv/bin/python scripts/run_admin_panel.py
```

| Adres | Ne |
|---|---|
| http://127.0.0.1:5050/call | 📞 Sesli arama ekranı (demonun kalbi) |
| http://127.0.0.1:5050/admin | Komuta Merkezi (KPI + Kurtarılan Hacim ₺) |
| http://127.0.0.1:5050/admin/outbound | Uyuyan İşletmeler → "AI Ara" |

Demo öncesi temiz başlangıç:

```bash
.venv/bin/python scripts/reset_demo.py --seed   # panel KAPALIYKEN
```

## Demo senaryoları

| # | Söyle | Ne olur |
|---|---|---|
| S1 | "Param ne zaman yatacak?" | Hakediş: net 44.104 TL, yarın 10:00, sonu 44 17 IBAN |
| S2 | "Dün 1.250 TL çektim, göremiyorum" | İşlemi bulur: onaylı, yarınki hakedişte — panik biter |
| S3 | "POS cihazım açılmıyor!" → "Denedim, olmadı" → "Evet gönder" | Adım adım arıza → servis kaydı → **ödeme linki upsell** |
| S4 | "Komisyon neden bu kadar yüksek?" | Veriyle açıklar + Esnaf Plus'a geçiş önerir (~900 TL/ay tasarruf) |
| S5 | "Instagram'dan satıyorum, kolay yolu yok mu?" | Sanal POS + ödeme linki cross-sell |
| S6 | "Yeter artık, şikayet edeceğim!" | Handoff: özetle temsilciye; panelde SLA kuyruğu |
| S7 | Panel → Uyuyan İşletmeler → **AI Ara** (Yıldız Cafe) | **Ada önce konuşur**, churn'ü öğrenir, sadakat planı sunar → kabulde Kurtarılan Hacim +143.333 TL |

Mock veride tarihler `D-1`/`D+1` token'larıyla göreli tutulur — demo verisi
hiçbir zaman bayatlamaz.

## Testler

```bash
.venv/bin/python -m pytest tests/ --ignore=tests/test_voice.py   # 83 test
```

Senaryo dispatch'leri (S1-S7), veri katmanı, call API, WhatsApp köprüsü ve
destek NLU'su sahte LLM'le deterministik test edilir.

## Güvenlik

- Müşteri tam kart numarası okumaya kalkarsa Ada **sözünü keser** (yalnız son 4 hane)
- Tüm tutar/tarih bilgisi araç verisinden gelir; prompt seviyesinde uydurma koruması
- `ADMIN_PASSWORD` doluysa panel Basic Auth ile korunur; köprü token'la kilitlenebilir
- `.env`, `data/admin.sqlite3` ve ses çıktıları `.gitignore`'dadır
