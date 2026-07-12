# Voice Agent — AI Destekli Emlak Satış Asistanı

Konut projesi satışı için **tam agentic** yapay zeka satış asistanı + satış ofisi CRM paneli.
Müşterilerle WhatsApp üzerinden Türkçe konuşur, stok/fiyat sorularını gerçek envanterden
yanıtlar, satın alma sinyali yakalayınca insan danışmana devreder. Yanında, Türk konut
projesi satış sürecine göre tasarlanmış tam donanımlı bir yönetim paneli gelir.

## Özellikler

### 🤖 AI Satış Asistanı
- **Tam agentic mimari**: sabit senaryo/akış yok — araç seçimini ve her cevabı LLM üretir
  (Router LLM → araç → cevap üretimi)
- Araçlar: envanter arama, fiyat sorgulama, insana devir (handoff), genel sohbet
- WhatsApp (whatsapp-web.js) ve sesli kanal (Whisper STT + ElevenLabs TTS) desteği
- Konuşma hafızası: yeniden başlatmada geçmiş veritabanından geri yüklenir
- Telefon/adres bilgileri yalnızca satış profilinden — uydurma koruması prompt seviyesinde

### 🖥️ Yönetim Paneli (CRM)
| Modül | Ne yapar |
|---|---|
| Komuta Merkezi | KPI'lar (mesai dışı yakalanan lead dahil), grafikler, sıcak lead listesi |
| Günün Brifingi | LLM'in panel verisinden yazdığı Türkçe sabah özeti |
| Stok Panosu | Blok/kat/daire renk kodlu matris, tek tık durum değişikliği |
| Opsiyon & Kapora | Süreli opsiyon (otomatik düşme), kapora kaydı, satış geçişleri |
| Müşteri Adayları | 7 aşamalı sürükle-bırak kanban, sıcaklık skorlama, AI aşama önerisi |
| Talep Eşleştirme | AI'ın çıkardığı tercihlere uyan daireleri skorlayıp önerir |
| Handoff Kuyruğu | İnsan bekleyen konuşmalar, SLA sayacı, tek tık devralma |
| Canlı Devralma | AI'ı duraklat, müşteriye panelden insan olarak WhatsApp yaz |
| Ödeme Planı | Peşinat/taksit/ara ödeme/vade farkı hesabı + yazdırılabilir teklif |
| Analitik | Dönüşüm hunisi, talep-stok baskısı, AI performansı, CSV export |
| Görevler | Otomatik takip listesi (temassız lead, dolan opsiyon) + manuel görevler |
| Test Sohbeti | Agent'ı WhatsApp'a gerek kalmadan panelden test et |

## Mimari

```
Telefon → WhatsApp Web → Node botu (whatsapp_mesaj_bot/)
      → Flask köprüsü :5051 (whatsapp/) → AgentOrchestrator (core/)
                                              ├── Router LLM (araç seçimi)
                                              ├── Araçlar (envanter/fiyat/handoff)
                                              └── Cevap LLM'i
Yönetim paneli :5050 (admin_panel/) ←→ SQLite (data/admin.sqlite3) + JSON veri (data/)
```

- **LLM**: Ollama (varsayılan) veya OpenAI — `core/llm.py`
- **Veri**: 212 dairelik envanter/fiyat/güneş JSON'ları + SQLite (konuşmalar, lead'ler,
  rezervasyonlar, teklifler, görevler)

## Kurulum

```bash
# 1. Python bağımlılıkları
pip install -r requirements.txt        # openai-whisper için ffmpeg gerekir

# 2. Node botu
cd whatsapp_mesaj_bot && npm install && cd ..

# 3. Yapılandırma
cp .env.example .env                   # LLM, panel parolası, köprü token'ı vb.

# 4. LLM (varsayılan mod)
ollama serve                           # ve .env'deki OLLAMA_MODEL'i indirin
```

## Çalıştırma

```bash
python server.py
```

Tek komut üç servisi başlatır ve sağlıklarını izler: admin paneli
(http://127.0.0.1:5050/admin), WhatsApp köprüsü (:5051) ve QR kodla eşleşen
WhatsApp botu. `Ctrl+C` hepsini kapatır.

## Testler

```bash
python -m pytest tests/ --ignore=tests/test_voice.py   # test_voice whisper/torch ister
```

## Güvenlik Notları

- `ADMIN_PASSWORD` doluysa panel HTTP Basic Auth ile korunur
- `WHATSAPP_BRIDGE_TOKEN` doluysa köprü uçları imzasız istekleri reddeder
- `data/admin.sqlite3` (gerçek konuşmalar) ve WhatsApp oturum dosyaları `.gitignore`'dadır

## Yol Haritası

Tamamlanan fazlar ve sıradaki adaylar için [docs/PANEL_ROADMAP.md](docs/PANEL_ROADMAP.md).
