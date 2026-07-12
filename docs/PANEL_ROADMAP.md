# Admin Panel Yol Haritası (CRM Dönüşümü)

> Temmuz 2026'da yapılan pazar araştırmasına dayanır: uluslararası emlak CRM'leri
> (Follow Up Boss, BoldTrail/kvCORE, Lofty, Spark, Sell.Do, Pipedrive, HubSpot) ve
> Türkiye pazarı (Novo CRM, Konutmatik, TikoCRM, TeamWork, Yapısoft SalesOffice,
> sahibinden Pro, Emlakjet, Rapitek) incelendi. Ayrıntılı bulgular konuşma
> geçmişinde; bu dosya uygulama planıdır.

## Temel içgörüler

1. **Türk proje-satış CRM'inin kalbi blok/kat/daire renk kodlu stok matrisi** —
   panelin "ciddiye alınmasını" tek başına sağlayan ekran (yeşil=satışta,
   turuncu=opsiyonlu, kırmızı=satıldı).
2. **Lead modeli üç boyutludur**: aşama (pipeline) + sıcaklık (hot/warm/cold) +
   skor (davranışsal). AI notlarımız (bütçe, tip, aciliyet, niyet) bu skorların
   girdisinin ta kendisi — çoğu CRM bu veriyi toplayamıyor, biz zaten topluyoruz.
3. **Türk satış ofisi süreci**: opsiyon (24-72s süreli kilit) → kapora → sözleşme
   → senetli ödeme planı. Ödeme planı hesaplayıcı + PDF teklif vazgeçilmez.
4. **"Mesai dışı yakalanan lead"** Türkiye'de AI asistan ürünlerinin ana satış
   argümanı — bizim panelde bunu kanıtlayan metrik öne çıkmalı.
5. En çok izlenen metrikler: speed-to-lead (<5 dk), randevu sayısı, kaynak bazlı
   dönüşüm hunisi, doluluk/absorpsiyon hızı (satış/ay ÷ kalan stok).

## Fazlar

### Faz 1 — Komuta Merkezi Dashboard'u ✅ KOLAY (veri hazır)
- KPI kartları: bugün/bu hafta yeni lead (trend oku ile), aktif konuşma, handoff
  sayısı, **mesai dışı yakalanan lead**, stok özeti (satışta/rezerve/satıldı + doluluk %).
- Grafikler: son 30 gün konuşma trendi, daire tipi bazlı talep dağılımı (AI
  notlarından), saatlik mesaj yoğunluğu.
- "Sıcak lead'ler" listesi (bütçe+aciliyet+handoff sinyaliyle) ve son aktivite akışı.
- Veri kaynağı: conversation_sessions/turns (timestamp'ler), user_ai_notes, inventory.

### Faz 2 — Stok Panosu (blok/kat/daire matrisi) ✅ KOLAY-ORTA (veri hazır)
- Blok sekmeleri (A-E), kat × daire renk kodlu grid, hücrede tip + fiyat kısaltması.
- Tıklayınca ünite kartı: m², cephe, güneş, fiyat, durum değiştirme.
- Tip/kat/cephe/durum filtreleri; doluluk sayacı.
- Not: "İlanlar" tablo görünümü kalır; bu onun görsel kardeşi.

### Faz 3 — Müşteri Adayları (Lead) modülü 🔶 ORTA (küçük DB eki)
- DB: user_ai_notes'a `stage`, `temperature`, `source`, `last_contact_at` kolonları.
- Lead listesi: sıcaklık rozeti, aşama, tercih özeti (tip/bütçe), son temas.
- Kanban görünümü: Yeni → Nitelikli → Randevu → Gösterim → Opsiyon/Kapora →
  Satış (+ Kayıp). Sürükle-bırak aşama değişimi; AI'dan otomatik aşama önerisi.
- Müşteri detayı: birleşik zaman çizelgesi (tüm konuşmalar + AI notları + manuel
  not + aşama değişimleri) — FUB'un "tek zaman çizelgesi" kalıbı.

### Faz 4 — Talep-Ünite Eşleştirme + Handoff Kuyruğu 🔶 ORTA
- Talep kartı: AI'ın çıkardığı tercihler yapılandırılmış kriter olarak (tip,
  bütçe aralığı, kat, cephe, güneş).
- Eşleşme motoru: kritere uyan "satışta" daireler skorlu top-3; müşteri detayında
  ve lead listesinde "eşleşen daire" rozeti.
- Handoff kuyruğu: "insan bekleyen" konuşmalar SLA sayacıyla (handoff'tan bu yana
  geçen süre), tek tıkla konuşmaya gitme.

### Faz 5 — Opsiyon/Rezervasyon + Kapora akışı 🔶 ORTA (Türk süreci)
- Üniteye süreli opsiyon: müşteri + bitiş zamanı; süre dolunca otomatik "satışta"ya dönme.
- Kapora kaydı: tutar, tarih, iade/iptal notu; durum geçiş kuralları
  (satışta→opsiyonlu→kaporalı→satıldı).
- Stok matrisi + lead kanbanı bu durumları renkleriyle gösterir; agent envanter
  durumunu zaten okuduğu için otomatik uyumlu.

### Faz 6 — Ödeme Planı Hesaplayıcı + PDF Teklif 🔶 ORTA
- Peşinat % + taksit sayısı + ara/balon ödeme → anında tablo; markalı PDF teklif çıktısı.
- Ünite kartından "teklif oluştur"; teklif geçmişi müşteri zaman çizelgesine düşer.

### Faz 7 — Analitik & Raporlar 🔶 ORTA
- Dönüşüm hunisi: konuşma → nitelikli → handoff → opsiyon → satış (oranlarla).
- Talep ısı haritası (tip×kat×cephe talebi vs kalan stok — fiyatlama kararı desteği).
- AI performansı: AI'ın tek başına çözdüğü oran, insana devir oranı, ortalama
  yanıt hacmi; CSV dışa aktarım.

### Faz 8 — Görevler & Takip Listesi 🔶 ORTA
- Otomatik günlük liste: 3+ gündür temassız sıcak lead, süresi dolmak üzere olan
  opsiyon, bekleyen handoff.
- Manuel görev + hatırlatma; dashboard'da "bugünün görevleri" kutusu.

### Faz 9+ — İkinci Halka 🔷 BÜYÜK
- ✅ Canlı devralma (Temmuz 2026): müşteri sayfasında "Devral (AI'ı duraklat)" —
  duraklatılan kullanıcıya AI cevap üretmez (mesajları loglanır), panelden yazılan
  mesajlar outbox kuyruğuna girer ve Node botu WhatsApp'tan iletir; "Devraldım"
  butonu da AI'ı otomatik duraklatır. Hazır yanıt kütüphanesi hâlâ aday.
- ✅ AI günlük brifing (Temmuz 2026): dashboard'daki "Günün Brifingi" kartı —
  KPI + sıcak lead + kuyruk + takip listesi + stok verisinden LLM Türkçe sabah
  özeti üretir; Oluştur/Yenile ile tetiklenir, son brifing saklanır.
- Toplu mesaj/segment kampanyası, broker portalı, sözleşme şablonları,
  kullanıcı/rol yönetimi, randevu takvimi.

## İlerleme
- [x] Faz 1 — Dashboard (Temmuz 2026: KPI kartları + trend, stok bandı, 30 günlük
  hacim grafiği, saatlik yoğunluk, tip bazlı talep, sıcak lead listesi, aktivite akışı)
- [x] Faz 2 — Stok panosu (Temmuz 2026: blok sekmeli kat×daire renk kodlu matris,
  durum/tip filtreleri, ünite kartı + tek tık durum değiştirme, doluluk sayaçları)
- [x] Faz 3 — Lead modülü (Temmuz 2026: 7 aşamalı sürükle-bırak kanban, sıcaklık
  rozetleri, AI aşama önerisi çipi, müşteri detayında aşama seçici + değişiklik
  geçmişi, lead_events tablosu)
- [x] Faz 4 — Eşleştirme + handoff kuyruğu (Temmuz 2026: tercih→daire skorlama
  motoru, müşteri detayında talep kartı + top-3 eşleşme, kanban'da eşleşme rozeti,
  SLA sayaçlı "İnsan Bekleyenler" kuyruğu + Devraldım akışı)
- [x] Faz 5 — Opsiyon/kapora (Temmuz 2026: süreli opsiyon + otomatik düşme,
  kapora kaydı, opsiyon→kapora→satış geçişleri, hücrelerde OPS/KAPORA rozeti,
  lead aşaması otomatik 'Opsiyon/Kapora'ya taşınır)
- [x] Faz 6 — Ödeme planı (Temmuz 2026: peşinat/taksit/ara ödeme/vade farkı
  hesaplayıcı + takvim, sunucu taraflı önizleme formu, markalı yazdırılabilir
  teklif sayfası, teklifler müşteri zaman çizelgesine işlenir)
- [x] Faz 7 — Analitik (Temmuz 2026: dönüşüm hunisi, tip/cephe bazlı talep-stok
  baskı tabloları, AI performans kartları (çözüm oranı, devir oranı), araç
  kullanım dağılımı, lead + konuşma CSV export)
- [x] Faz 8 — Görevler (Temmuz 2026: otomatik takip listesi — bekleyen handoff,
  dolmak üzere opsiyon, 3+ gün temassız sıcak/ılık lead — + manuel görevler,
  son tarih/gecikme takibi, dashboard'da Bugünün Görevleri kutusu)

**🏁 Roadmap'in 8 fazı da tamamlandı (Temmuz 2026). Sıradaki aday işler için
"Faz 9+ — İkinci Halka" bölümüne bakın.**

**Tur 2 — ekran içi UX derinleştirme (sol menü, İlanlar, Stok Panosu) ayrı planda:
[UX_ROADMAP.md](UX_ROADMAP.md)**
