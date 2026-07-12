# WhatsApp Mesaj Botu

Bu klasor, demo amacli gayriresmi bir WhatsApp Web bridge'i icerir.

Akis:
- QR terminalde uretilir
- Sen kendi WhatsApp hesabini baglarsin
- Gelen mesaj Python bridge'ine gider
- Python agent cevabi geri doner
- Bot cevabi WhatsApp'tan yollar

## Kurulum

```bash
cd whatsapp_mesaj_bot
npm install
```

## Calistirma

Python tarafinda su servisler acik olmali:
- admin panel: `python scripts/run_admin_panel.py`
- WhatsApp bridge: `python scripts/run_whatsapp_bridge.py`

Sonra:

```bash
cd whatsapp_mesaj_bot
npm start
```

Varsayilan Python bridge adresi:

```text
http://127.0.0.1:5051/whatsapp/message
```

Farkli adres kullanmak istersen:

```bash
export WHATSAPP_AGENT_URL=http://127.0.0.1:5051/whatsapp/message
npm start
```

Auth klasoru sabit olarak burada tutulur:

```text
whatsapp_mesaj_bot/.wwebjs_auth
```

QR'yi tekrar almak icin gerekirse bu klasoru temizleyebilirsin.

## Not

Bu cozum resmi WhatsApp Business API degildir. Demo/PoC icin uygundur; uretim icin onerilmez.
