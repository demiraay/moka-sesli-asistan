# Panel UX Yol Haritası — Tur 2 (Temmuz 2026)

> Kaynak: kullanıcının üç şikayeti (sabit sol menü, zayıf İlanlar sayfası, arama/analizsiz
> Stok Panosu) + her alan için ayrı derinlemesine kod/UX analizi. Her adım tek commit
> boyutundadır; her adım sonunda testler koşulur ve push edilir.

## Kesinleşen tasarım kararları

1. **Türkçeleştirme yalnız görünüm katmanında.** JSON'lardaki `available / South-West / high`
   değerleri AI agent'ın arama sözleşmesidir (`core/inventory.py` `_matches_criteria`,
   `core/tools.py` eşlemeleri) — veriye dokunulmaz; çeviri Jinja filter'larıyla yapılır:
   `tl` (15.920.000 TL), `status_tr`, `direction_tr`, `sun_tr`.
2. **Fotoğraflar SQLite + `data/uploads/`ta yaşar, JSON'da değil.** Meta `listing_photos`
   tablosunda; dosyalar `data/uploads/listings/<ref>/` altında (gitignore'lu). Servis
   `/admin/media/...` route'u üzerinden — çünkü `/admin-static` **Basic Auth dışında**.
3. **Tip fotoğrafı devralma.** 212 daireye tek tek foto gerçekçi değil: 5 daire tipine
   birer set (~30 dosya) yüklenir, daireler tipinin setini devralır; daireye özel foto
   yüklenirse onu ezer. Foto yoksa tip bazlı renkli placeholder kart.
4. **Sidebar 260px ↔ 72px ikon modu** (tam gizleme değil), inline SVG ikonlar (CDN yasak),
   durum `localStorage` + head'de FOUC guard, kısayol **Ctrl+B** (Türkçe Q klavyede `[`
   AltGr gerektirir, kullanılamaz). ≤900px'te off-canvas drawer + hamburger üst bar.
5. **`listing_status_log` şart.** Durum değişiklikleri bugün hiçbir yerde loglanmıyor
   (`update_listing_status` iz bırakmaz, `release_expired_options` onu bypass bile ediyor)
   → satış hızı/absorpsiyon ancak log açıldıktan sonra birikir; kart o zamana dek dürüst
   "veri birikiyor" gösterir.
6. **Stok araması "blok+kapı" biçimini de desteklemeli** — kapı numaraları bloklar arası
   çakışıyor ("101" beş blokta da var).
7. **Nav rozetleri context_processor + 30 sn TTL cache** — `get_handoff_queue` tüm
   `conversation_turns` tablosunu tarıyor, cache'siz her sayfa yüklemesine binerdi.

---

## Alan 1 — Sol Menü / Yerleşim (7 adım)

- [x] **A1** Nav ikonları + hover/focus: 9 inline SVG (stroke, `currentColor`), `.nav-link`
      flex yapısı; görsel riski sıfır hazırlık adımı.
- [x] **A2** Aktif sayfa vurgusu: `request.endpoint` → nav eşlemesi (alt sayfalar üst öğeye:
      `user_conversations`→Müşteri Adayları, `new_offer/offer_detail`→Stok Panosu,
      `conversation_detail`→Konuşmalar); inset sol çizgi + `aria-current`.
- [x] **A3** Sticky sidebar: `position:sticky; top:0; height:100vh; overflow-y:auto` —
      uzun sayfada menü artık kaybolmaz.
- [x] **A4** Daraltma çekirdeği: toggle brand satırında, `admin.sidebar.collapsed`
      localStorage anahtarı, FOUC guard, Ctrl+B, 160ms geçiş; daralınca kanban ~1 kolon,
      stok matrisi ~2 daire kolonu daha gösterir.
- [x] **A5** Mobil/tablet: off-canvas drawer + hamburger üst bar + backdrop; drawer durumu
      localStorage'a yazılmaz (her sayfa kapalı başlar), Esc/backdrop kapatır.
- [x] **A6** Nav rozetleri: Müşteri Adayları'na kırmızı **bekleyen handoff** sayısı,
      Görevler'e açık görev sayısı; `claim_handoff`/`toggle_task` cache'i anında düşürür;
      ikon modunda köşe noktasına dönüşür.
- [x] **A7** Nav gruplama + iş akışı sırası: **Operasyon** (Panel, Müşteri Adayları,
      Konuşmalar, Görevler) / **Envanter** (Stok Panosu, İlanlar) / **Araçlar** (Analitik,
      Test Sohbeti, Satış Profili).

P2 (sonra): `<title>` kalıbı standardizasyonu, brand'in "Ekinciler Residence" olması +
footer'da ofis bilgisi, müşteriye sunum modu (tam gizleme), denormalize handoff sayacı.

---

## Alan 2 — İlanlar (6 adım)

- [x] **B1** TR görünüm katmanı: `tl/status_tr/direction_tr/sun_tr` filter'ları; liste
      kolonları yenilenir — "A-405" başrolde (INV-#### altta gri), tip + **net/brüt m²**
      (veri `flats.json`'da hazırdı, hiç gösterilmiyordu), TL fiyat + m² birim fiyatı,
      renkli durum rozeti (+ OPS kalan süre), kat·cephe Türkçe; **Sil listeden kalkar**
      (212 gerçek daire için satır başı kırmızı buton veri kaybı riski).
- [x] **B2** Filtre çubuğu (blok/tip/durum/fiyat aralığı) + kolon sıralama + sayfalama
      (30/sayfa); `list_listings(query, filters=None, sort=None)` geriye uyumlu genişler.
- [x] **B3** İlan detay sayfası `GET /admin/listings/<id>` (sahibinden uyarlaması, fotosuz
      iskelet): sol galeri alanı (şimdilik placeholder), sağda büyük TL fiyat + m² birim
      fiyat + durum + aktif rezervasyon banner'ı + hızlı aksiyonlar (mevcut
      `/admin/stock/<id>/status|option|deposit|release` API'lerine fetch — yeni route
      gerekmez) + "Teklif oluştur"; altta Türkçe özellik tablosu (kat "4/15", blok tipi,
      güneş saatleri) ve **dairenin geçmişi** (`get_unit_history`: tüm rezervasyonlar +
      teklifler + lead olayları tek kronolojik akışta); Sil buraya taşınır (metin onaylı).
- [x] **B4** Foto sistemi: `listing_photos` tablosu (scope `unit|flat_type`), upload/sil/
      kapak route'ları, `/admin/media/...` auth'lu servis, tip devralma +
      `flat_type_photos.html`, magic-byte doğrulama + `MAX_CONTENT_LENGTH` 10MB, galeri
      JS (ok tuşları), listede thumbnail, placeholder macro, `.gitignore`'a `data/uploads/`.
- [x] **B5** Edit form düzeltmeleri: `block_id`/`direction` serbest metinden **select**'e
      (yazım hatası agent eşleşmesini sessizce bozuyor — gerçek veri bütünlüğü riski),
      fiyat girişine binlik ayraç, "Açıklama" etiket karmaşasının çözülmesi (mevcut alan
      aslında `sunlight.description`'a yazıyor) + inventory'ye opsiyonel `description`
      alanı (agent'a `enrich_details` ile otomatik akar).
- [x] **B6** (P1 paketi) Fiyat değişiklik logu (`listing_price_log` + listede ↓/↑ rozeti),
      **eşleşen adaylar** (daire→lead tersine eşleşme: "bu daireyi kime satabilirim"
      telefon listesi), `?view=cards` kart görünümü, CSV export.

P2: yazdırılabilir müşteri özeti (offer_print kalıbı), WhatsApp'tan foto gönderme (outbox
TEXT-only — Node bot MessageMedia işi, ayrı paket), m² fiyatının blok ortalamasıyla kıyası.

---

## Alan 3 — Stok Panosu (8 adım)

- [x] **C1** Store zenginleştirme: `get_stock_board`'a m² (net/brüt), kat satırı sayaçları,
      `type_summary` (tip başına satışta/toplam + ort. fiyat), `expiring_options` (tüm
      bloklar), cephe/güneş filtre seçenekleri, `expiring_soon` bayrağı + alan testleri.
- [x] **C2** Arama + atlama (P0): kapı no / INV / **blok+kapı** ("A-101") → doğru sekmeye
      otomatik geç, hücreye kaydır (`inline:'center'` — grid yatay kayıyor), pulse vurgusu,
      çoklu eşleşmede "2/5" sayacı + Enter ile gezinme, Esc temizler, `?q=` derin link.
- [x] **C3** Analiz şeridi (P0): tıklanabilir tip çipleri ("Satışta → 2+1: 34 · 3+1: 51"),
      kat etiketinde doluluk rozeti ("3/4 boş"), **24 saat içinde dolan opsiyonlar şeridi**
      (tıkla → hücreye atla) + hücrede ⏳ ikonu.
- [x] **C4** Ek filtreler + URL kalıcılığı: cephe (TR etiketli), güneş, kat aralığı, fiyat
      aralığı; `URLSearchParams` + `history.replaceState` → görünüm paylaşılabilir ve
      `location.reload()` sonrası filtreler artık sıfırlanmaz; "Filtre: 12 daire" sayacı.
- [x] **C5** Hover tooltip: m², cephe TR, güneş saatleri, tam fiyat, opsiyon bitişi;
      `position:fixed` (grid `overflow-x:auto` kırpmasın), dokunmatikte kapalı.
- [x] **C6** Isı haritası modları: **Durum** (mevcut) / **Fiyat** (sunucuda kartil bandı →
      gradyan) / **Talep** (AI notlarından ünite başına eşleşen lead sayısı —
      `get_demand_heat` + `_score_listing_for_notes` refactor'u); lejant + `?mode=` URL'de.
- [x] **C7** `listing_status_log` tablosu: `update_listing_status(source=...)` genişler,
      `release_expired_options`'ın JSON'a doğrudan yazan bypass'ı ve `update_listing`
      durum değişimleri de loglanır + testler.
- [x] **C8** Satış hızı + talep-stok baskısı: `get_sales_velocity` ("son 30 gün: X satış →
      stok ~Y ayda erir", veri birikene dek dürüst boş-durum kartı) + `get_type_pressure`
      baskı çipleri ("2+1: 12 talep / 34 stok").

P2: stok CSV + yazdırma görünümü, müşteri numarasına göre arama, ok tuşlarıyla hücre gezinme.

---

## Uygulama sırası

**Alan 1 → Alan 2 → Alan 3** (kullanıcının sıralamasıyla aynı; Alan 1 en küçük, hızlı
kazanım). Her adımda: `~/miniconda3/bin/python -m pytest tests/ -q --ignore=tests/test_voice.py`
yeşil + agent sözleşmesi kontrolü (inventory/config/prompt testleri) + commit + push.

**🏁 Üç alan da tamamlandı (Temmuz 2026):** Alan 1 (7 adım), Alan 2 (6 adım),
Alan 3 (8 adım) = 21 adım, hepsi ayrı commit, 131 test yeşil.

**✅ P2 turu (Temmuz 2026, 6 iş):**
- P2-1 Panel kimliği: marka "Ekinciler Residence" (projeler.json), sidebar footer ofis
  bilgisi, tüm sekme başlıkları "<sayfa> — <proje>".
- P2-2 Sunum modu: menüyü tümden gizler (Ctrl+Shift+P / buton), Esc ile çıkar, kalıcı değil.
- P2-3 Yazdırılabilir müşteri bilgi föyü (`/admin/listings/<id>/print`): kapak + özellik
  tablosu + ofis iletişim, print CSS.
- P2-4 m² fiyatının blok ortalamasıyla kıyası (detay fiyat kartında pazarlık kozu).
- P2-5 Stok CSV export + yazdırma görünümü (tüm bloklar alt alta, print CSS).
- P2-6 Müşteri numarasına göre stok araması (rezervasyon müşterisiyle eşleşme).

**Kalan P2 adayları (isteğe bağlı):** WhatsApp'tan müşteriye foto gönderme (Node bot
MessageMedia — ayrı paket), ok tuşlarıyla hücre gezinme, denormalize handoff sayacı.
