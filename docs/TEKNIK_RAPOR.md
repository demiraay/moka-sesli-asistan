# Moka Sesli Asistan: Ödeme Kuruluşları İçin Konuşma Tabanlı, Gelir Odaklı Yapay Zekâ Destek Hattı

**Teknik Rapor — Tasarım, Uygulama ve Kavram Doğrulama Değerlendirmesi**

Proje Kategorisi: FinTech / Dijital Çözümler ve Yenilikçi Teknolojiler
Kapsam: Moka United FinTech Hackathon — "Hack the Idea"
Tarih: Temmuz 2026

---

## Özet

Çağrı merkezleri, işletmelerin müşterileriyle kurduğu en doğrudan temas noktası
olmasına karşın, büyük ölçüde insan gücüne dayalı, maliyetli ve verimsiz bir
yapıda işlemektedir. Türkiye'de 2024 itibarıyla 68,5 milyar TL hacme ulaşan ve
167 binin üzerinde kişiyi istihdam eden bu sektörde [18], yanıtlanamayan ve terk
edilen çağrılar nedeniyle işletmeler gelirlerinin %10–30'unu kaybetmektedir
[2, 3]. Ödeme kuruluşları özelinde tablo daha da belirgindir: üye işyeri destek
hatlarına gelen çağrıların önemli bölümü, cevabı işlem veritabanında hazır olan
rutin sorgulardan (hakediş zamanı, işlem durumu, cihaz arızası, komisyon
kesintisi) oluşmakta; buna karşın arayan işletme sahibi IVR menüleri ve bekleme
kuyruklarında dakikalar kaybetmektedir.

Bu çalışmada, bir ödeme kuruluşunun üye işyerlerine telefon üzerinden hizmet
veren, araç çağırma (tool-calling) mimarisine dayalı, konuşma tabanlı bir yapay
zekâ destek sistemi (Ada) sunulmaktadır. Sistem; düşük gecikmeli konuşma tanıma
(Whisper large-v3-turbo), iki kademeli büyük dil modeli diyalog yönetimi
(yönlendirici + yanıt modeli), doğal Türkçe ses sentezi ve gerçek işlem verisine
bağlı dokuz alan aracından oluşan modüler bir mimari üzerine kurulmuştur.
Sistemi mevcut çözümlerden ayıran temel katkılar; oturumlar arası süreklilik
taşıyan yapısal "müşteri kartı" bağlam takibi, finansal alanlarda model
uydurmasına izin vermeyen veriye dayalı yanıt ilkesi ve destek hattını maliyet
merkezinden gelir kanalına dönüştüren kazanım katmanıdır (çözüm sonrası bağlama
uygun teklif üretimi ile işlem hacmi düşen işletmelerin proaktif aranması).

Yedi temsilî senaryo üzerinden yürütülen kavram doğrulama (PoC) çalışmasında,
uçtan uca yanıt turu süresi (konuşma tanıma, iki model çağrısı ve ses sentezi
dâhil) ortalama 2–3 saniye aralığında ölçülmüş; tüm senaryolar hedeflenen araç
zincirini üretmiş ve 94 maddelik otomatik test kümesi eksiksiz doğrulanmıştır.
Temsilî geri kazanım senaryosunda tek bir proaktif aramayla aylık 143.333 TL
işlem hacminin sisteme geri kazandırılması panele parasal karşılığıyla
işlenmiştir. Bulgular, önerilen yaklaşımın rutin çağrı süreçlerini
otomatikleştirerek operasyon maliyetlerini düşürme [4, 17] ve aynı kanaldan
ölçülebilir gelir üretme potansiyeli taşıdığını göstermektedir.

**Anahtar Kelimeler:** sesli yapay zekâ asistanı, çağrı merkezi otomasyonu,
araç çağırma, ödeme sistemleri, üye işyeri desteği, proaktif geri kazanım,
müşteri deneyimi

---

## İçindekiler

1. Giriş
2. İlgili Çalışmalar ve Mevcut Çözümler
3. Problem Tanımı ve Hedef Kitle Analizi
4. Önerilen Sistem
5. Yöntem: Ayırt Edici Bileşenler
6. Kavram Doğrulama Çalışması ve Bulgular
7. Risk Analizi
8. Pazar Analizi, Etki ve Sürdürülebilirlik
9. Sonuç ve Gelecek Çalışmalar
Kaynakça

---

## 1. Giriş

Müşteri hizmetleri, işletmelerin marka algısını ve müşteri bağlılığını doğrudan
şekillendiren stratejik bir fonksiyondur. Buna karşın sektörün işleyişi,
teknolojik gelişmelere rağmen büyük ölçüde geleneksel kalmıştır: karmaşık ve
uzun IVR menülerinin çağrı terk oranlarını %40'a kadar artırabildiği raporlanmış
[2, 3]; büyük operasyonlara sahip şirketlerin dahi terk edilen çağrılar
nedeniyle gelirlerinin %10–30'unu kaybettiği gözlenmiştir. Türkiye'de müşteri
hizmetleri ve çağrı merkezi sektörü 2024'te bir önceki yıla göre %64 büyüyerek
68,5 milyar TL hacme ulaşmış, ancak yapay zekâ kullanımı ağırlıklı olarak IVR
tabanlı çağrılarla sınırlı kalmış; proaktif yapay zekâ temelli etkileşimler
yalnızca %8 düzeyinde ölçülmüştür [18]. McKinsey'nin değerlendirmeleri, rutin
işlemlerin otomasyonunun müşteri hizmetleri maliyetlerini %30'a kadar
azaltabileceğini öngörmektedir [4].

Ödeme sektörü bu tablonun en keskin örneklerinden biridir. Üye işyeri destek
hattını arayan bir esnafın sorusu çoğunlukla üç kalıptan birine girer: "Param ne
zaman yatacak?", "Şu işlemi göremiyorum", "POS cihazım çalışmıyor". Bu soruların
tamamının cevabı kurumun işlem veritabanında hazırdır; yanıt gecikmesinin her
dakikası hem operasyon maliyeti hem de — cihazı arızalı işletme satış
yapamadığından — doğrudan işlem hacmi kaybıdır.

Bu çalışmanın amacı, ödeme kuruluşlarının üye işyeri destek hattını, doğal
Türkçe konuşan, yanıtlarını gerçek işlem verisine dayandıran ve destek temasını
gelir üretimine bağlayan bir sesli yapay zekâ asistanıyla dönüştürmektir.
Çalışmanın başlıca katkıları şu şekilde özetlenebilir:

1. Yönlendirici ve yanıt modellerini ayrıştıran, dokuz alan aracına dayalı
   **araç çağırma mimarisi** ile finansal sorguların gerçek veriden, denetlenebilir
   biçimde yanıtlanması;
2. Oturumlar arası süreklilik taşıyan, yapısal **"müşteri kartı"** bağlam takibi
   (güncel sorun, anılan tutar/tarih, ruh hâli, satış fırsatı) ve hattan gelen
   kimlik varsayımıyla kimlik sorusunun tamamen kaldırılması;
3. Destek hattını gelir kanalına dönüştüren **kazanım katmanı**: çözüm sonrası
   bağlama uygun tek teklif üretimi, işlem hacmi düşen işletmelerin panelden tek
   adımla **proaktif aranması** ve kabul edilen tekliflerin "kurtarılan hacim"
   metriğiyle parasal izlenmesi;
4. Sesli okumaya uyum katmanı: maskeli IBAN/kart ifadelerinin, yüzdelerin ve
   bağlantıların **konuşma diline dönüştürülmesi**;
5. Gerçekçi bir kullanım senaryosu kümesi üzerinden yürütülen kavram doğrulama
   çalışmasıyla sistemin Türkçe başarımının nicel raporlanması.

## 2. İlgili Çalışmalar ve Mevcut Çözümler

Müşteri hizmetlerinde diyalog sistemleri, kural tabanlı IVR yapılarından sohbet
robotlarına ve son dönemde büyük dil modeli destekli sesli asistanlara doğru
evrilmiştir. IBM'in bilişsel etkileşim çalışmaları, chatbot teknolojilerinin
rutin taleplerde etkili olduğunu ancak karmaşık ve duygusal yük taşıyan
etkileşimlerde sınırlı kaldığını göstermektedir [5]; empatik yanıt sistemlerinin
müşteri memnuniyetine katkısı ampirik olarak ortaya konmuştur [10].

Ticari pazarda Google Dialogflow, Voiceflow, LivePerson ve Türkiye'de AloTech
öne çıkmaktadır; yurt dışında Synthflow, OpenCall ve Leaping gibi platformlar
çoğunlukla önceden tanımlanmış diyalog akışlarına dayanmaktadır. Bu çözümlerin
ortak sınırlılığı, sektörel veri katmanıyla bütünleşik çalışmamaları ve destek
temasını gelir üretimine bağlayan bir katman içermemeleridir. Önerilen sistem;
ödeme alanına özgü araç kümesi, uydurma korumalı veri erişimi ve proaktif geri
kazanım akışını tek mimaride birleştirmesi bakımından ayrışmaktadır.

### 2.1 Sektördeki güncel gelişmeler (2025–2026)

Sesli yapay zekâ ajanları alanı hızlı bir olgunlaşma sürecindedir. OpenAI 2025'te
gerçek zamanlı konuşma-konuşma modelini ve SIP tabanlı telefon desteğini
duyurmuş; Amazon Nova Sonic'i, Talkdesk 59 dil destekli karar alabilen
asistanlarını, RingCentral no-code stüdyolu AIR Pro'yu piyasaya sürmüştür [12].
Sektör raporları saniye altı gecikmenin "doğal" konuşma eşiği hâline geldiğini,
giden (outbound) arama ajanlarının en hızlı büyüyen segment olduğunu ortaya
koymaktadır [12, 13]; 2025'te sesli ajan kullanımının yaklaşık dokuz kat arttığı
raporlanmıştır [14]. Bu gelişmeler, önerilen sistemin tasarım tercihlerinin —
düşük gecikmeli sıra tabanlı akış, söz kesme desteği, giden arama otomasyonu ve
dikey (ödeme) senaryo derinliği — sektörün yöneldiği doğrultuyla örtüştüğünü
göstermektedir. Büyük sağlayıcıların alana girişi, farklılaşmanın model
erişiminden çok alan derinliği ve veri bütünleşmesi katmanlarında aranması
gerektiğine işaret etmektedir; önerilen sistemin katkıları bu katmanlarda
konumlanmaktadır.

### 2.2 Düzenleyici çerçeve

Avrupa Birliği Yapay Zekâ Yasası'nın sesli yapay zekâ ajanlarını kapsayan
şeffaflık yükümlülükleri 2 Ağustos 2026'da yürürlüğe girmekte; ABD'de FCC'nin
Şubat 2024 kararı, aramalarda yapay zekâ üretimi ses için önceden açık onay
şartı getirmektedir [13]. Finansal veri işleyen bir sistem için KVKK/GDPR uyumu
ve kart verisi güvenliği (PCI-DSS ilkeleri) tasarım aşamasından itibaren mimari
ilke olarak benimsenmiştir [11]: asistan tam kart numarası dinlemeyi reddeder,
kart verisi hiçbir katmanda açık biçimde tutulmaz ve tüm yanıtların veri kaynağı
arayüzden denetlenebilir.

## 3. Problem Tanımı ve Hedef Kitle Analizi

Sistem tasarımına temel oluşturmak üzere ödeme kuruluşu ekosistemindeki üç
paydaş grubunun ihtiyaçları analiz edilmiştir.

**Üye işyerleri (esnaf ve KOBİ).** Temsilî örnek, aylık 180 bin TL POS cirosu
olan bir gıda perakendecisidir. Başlıca sorunlar; hakediş ve işlem sorguları
için uzun bekleme süreleri, cihaz arızalarında satış kaybı ve mesai dışı
saatlerde desteğe erişilememesidir. Segmentin ihtiyacı, beklemesiz ve doğal
dille erişilebilen, işlem verisine hâkim bir destek kanalıdır.

**Destek operasyonu.** Çağrı hacminin önemli bölümü düşük katma değerli rutin
sorgulardan oluşmakta, temsilci kapasitesi gerçek vakalara (itiraz, güvenlik,
karmaşık arıza) ayrılamamaktadır. İhtiyaç; rutin çağrıları güvenle otomasyona
devreden, sınır durumlarında görüşmeyi özetiyle insana aktaran bir sistemdir.

**Büyüme ve satış ekipleri.** İşlem hacmi düşen işletmeler çoğunlukla sessizce
rakibe geçmekte, kayıp ancak aylık raporlarda fark edilmektedir. İhtiyaç; riskli
işletmelerin otomatik tespiti ve ölçeklenebilir bir geri kazanım kanalıdır.

## 4. Önerilen Sistem

### 4.1 Genel mimari

Sistem üç ana katmandan oluşur: tarayıcı tabanlı çağrı istemcisi, çağrı API'si
ve araç çağırma mimarisine dayalı diyalog çekirdeği. Çağrı akışı şu şekilde
işler: arayanın konuşması ses etkinliği algılama (VAD) ile tuşsuz olarak
yakalanır ve konuşma tanıma modülünde metne dönüştürülür; yönlendirici model,
ifadeyi ve görüşme bağlamını değerlendirerek çağrılacak aracı ve argümanlarını
yapısal biçimde üretir ve aynı adımda müşteri kartını günceller; seçilen araç,
işlem/hakediş/cihaz veri katmanından sonucu getirir; yanıt modeli bu yapısal
bağlamdan sesli okumaya uygun kısa Türkçe yanıtı üretir; yanıt, düşük gecikmeli
ses sentezi ile arayana iletilir. Asistan konuşurken arayan söze girerse ses
kesilir ve dinlemeye dönülür (barge-in). Asistanın yetersiz kaldığı veya risk
sinyali algılanan durumlarda görüşme, o ana kadarki özetiyle insan temsilciye
aktarılır.

| Modül | Teknoloji | Ölçülen başarım |
| --- | --- | --- |
| Ses Tanıma (STT) | Whisper large-v3-turbo (Groq API) + yerel Whisper yedeği; alan sözlüğü istemi | ~0,4 sn/tur; alan terimlerinde belirgin doğruluk artışı |
| Yönlendirici Model | gpt-oss-20b (JSON kipinde araç/argüman seçimi + müşteri kartı) | ~0,6–1,0 sn; senaryo kümesinde hedeflenen araç seçiminde tam isabet |
| Yanıt Modeli | gpt-oss-120b (konuşmaya uygun Türkçe üretim) | ~1,0–1,5 sn; akıcı, veriye bağlı yanıtlar |
| Ses Sentezi (TTS) | ElevenLabs düşük gecikmeli model; katalogdan seçilebilir ses | ~0,6–0,8 sn |
| Uygulama Katmanı | Flask (çağrı API'si + yönetim paneli), SQLite | Tek komutla ayağa kalkan bütünleşik servis |
| Veri Katmanı | Üye işyeri / işlem / hakediş / cihaz / plan kayıtları; göreli tarih çözümü | Deterministik, zamanla bayatlamayan gösterim verisi |

**Tablo 1:** Sistem modülleri ve kavram doğrulama ortamında ölçülen başarım.

Kota ve kesinti dayanıklılığı üç kademede sağlanır: birincil API anahtarı hız
sınırına takıldığında yedek anahtara, o da erişilemezse yerel modellere düşülür;
herhangi bir araç hatası çağrıyı sonlandırmaz, asistan bilgiyi yeniden ister.

### 4.2 Fonksiyonel yetenekler

**Gelen çağrı otomasyonu.** Hakediş sorgusu, işlem arama (tutar/tarih/kart son
dört hanesi ile), cihaz arıza giderme (bilgi bankası adımlarının tek tek
yürütülmesi; çözümsüzlükte otomatik servis kaydı), komisyon açıklama ve dönem
ekstresi gönderimi insan müdahalesi olmaksızın tamamlanır.

**Giden arama ve geri kazanım.** Son ay cirosu önceki üç ayın ortalamasının
%30'unun altına düşen işletmeler panelde listelenir; tek adımla başlatılan
aramada ilk sözü asistan alır, ayrılma nedenini öğrenir ve tanımlı sadakat
teklifini sunar. Kabul, panele aylık hacim karşılığıyla ("kurtarılan hacim")
işlenir ve işletme listede kazanıldı olarak işaretlenir.

**Bağlama uygun teklif üretimi.** Çözülen her çağrıda en fazla bir teklif
değerlendirilir: cihazı arızalanan işletmeye geçici ödeme linki, cirosu büyüyen
işletmeye daha uygun komisyon planı, sosyal medyadan satış yapana sanal POS.

**Gerçek zamanlı analitik ve KPI takibi.** Günlük çağrı sayısı, insana
devredilmeden çözülen görüşme oranı, devir kuyruğu (SLA sayaçlı), kurtarılan
hacim ve araç kullanım dağılımı tek panelden izlenir; görüşme dökümlerinde her
yanıtın hangi araçla üretildiği görülebilir.

**Ses kişiselleştirme.** Asistanın sesi, ön dinlemeli bir katalogdan çağrı
bazında veya panelden varsayılan olarak seçilebilir.

### 4.3 Erişilebilirlik ve kullanım kolaylığı

Sistem tek komutla ayağa kalkar; çağrı istemcisi tarayıcıda çalışır ve teknik
bilgi gerektirmez. WhatsApp, ikincil kanal olarak aynı diyalog çekirdeğine
bağlıdır; görüşme bağlamı kanallar arasında korunur.

## 5. Yöntem: Ayırt Edici Bileşenler

### 5.1 Yapısal bağlam takibi ("müşteri kartı")

Yönlendirici model her turda, ek model çağrısı gerektirmeden, yapısal bir
müşteri kartı üretir: güncel sorun, anılan tutar ve tarih, terminal ve kart
bilgisi, ruh hâli ve olası satış fırsatı. Kart sonraki turlarda her iki modele
otoriter bağlam olarak sunulur; görüşme geçmişiyle çeliştiğinde kart esas
alınır. Arayanın kimliği hat üzerinden belirlendiğinden (CTI yaklaşımı) sistem
kimlik bilgisi sormaz; regex tabanlı bir çıkarım katmanı (tutar, gün adı,
terminal, son dört hane) modelin argüman eksiklerini tamamlayan anlamsal yedek
olarak çalışır.

### 5.2 Veriye dayalı yanıt ve uydurma koruması

Finansal alanlarda model çıktısına güvenilmez: yanıttaki her tutar, tarih ve
durum bilgisi araç katmanından gelir. Sayısal argümanlar tür ve biçim
dönüşümünden geçirilir (ör. "1.250" binlik ayracının ondalık olarak
yorumlanmasının engellenmesi); araç hatası özürlü ve veri istemeyen bir yanıtla
karşılanır; veri yoksa asistan bunu açıkça belirtir veya devir önerir.

### 5.3 Destekten gelire: kazanım katmanı

Sistemin ekonomik farkı, her destek temasını bir gelir fırsatına bağlamasıdır.
Kabul edilen teklifler olay günlüğüne parasal karşılığıyla yazılır ve panelde
birikimli "kurtarılan hacim" göstergesine dönüşür; böylece yatırım getirisi
tartışması tahmin yerine ölçüme dayanır.

### 5.4 Sesli okuma uyumu

Yanıtlar "kulak için" son işlemden geçer: maskeli IBAN "sonu 44 17 ile biten
IBAN" biçiminde, kart "4832 ile biten kart" olarak ifade edilir; bağlantı
adresleri sesli okunmaz; yüzdeler yazıyla, tutarlar doğal söyleyişle üretilir.
Konuşma tanıma katmanına alan sözlüğü (asistan adı, "hakediş", "POS" vb.) istem
olarak verilir; kayıt, eşik aşıldığı anda başlatılarak kelime başı kırpılması
önlenir.

### 5.5 Ölçülü otonomi ve insan devri

Öfke, dolandırıcılık şüphesi, ters ibraz, hukuki ifade, hesap kapatma talebi
veya iki kez çözülemeyen arıza durumunda görüşme, özetiyle birlikte SLA sayaçlı
devir kuyruğuna düşer. Devir eşiği kural + model işbirliğiyle belirlenir: duygu
sinyalleri bağlamı besler, nihai kararı yönlendirici model verir.

### 5.6 Veri güvenliği ve mevzuat uyumu

Arayan tam kart numarası okumaya başlarsa asistan sözü keserek yalnızca son
dört hanenin yeterli olduğunu belirtir. API anahtarları depoya dâhil edilmez;
panel parola ile korunabilir; WhatsApp köprüsü istek imzalama jetonuyla
kilitlenebilir. Tasarım KVKK/GDPR ilkeleriyle uyumludur [11].

## 6. Kavram Doğrulama Çalışması ve Bulgular

### 6.1 Çalışma tasarımı

Sistem, gerçek ödeme altyapısını temsil eden sentetik bir veri kümesi (18 üye
işyeri; işlem, hakediş, cihaz ve plan kayıtları) üzerinde, gerçek konuşma
tanıma, dil modeli ve ses sentezi servisleriyle uçtan uca değerlendirilmiştir.
Değerlendirme yedi temsilî senaryo üzerinden yürütülmüştür: (S1) hakediş
sorgusu, (S2) kayıp işlem araması, (S3) cihaz arızası → servis kaydı → ödeme
linki teklifi, (S4) komisyon itirazı → plan önerisi, (S5) sanal POS çapraz
satışı, (S6) öfkeli müşteri → insan devri, (S7) proaktif geri kazanım araması.
Ek olarak kart güvenliği müdahalesi, alakasız/belirsiz ifadeler ve insan talebi
gibi sınır durumlar test edilmiştir.

### 6.2 Bulgular

| Ölçüt | Sonuç |
| --- | --- |
| Uçtan uca yanıt turu (STT + 2 model + TTS) | Ortalama 2–3 sn; en iyi turlarda ~2,0 sn |
| Senaryo başarımı (hedeflenen araç zinciri) | 7/7 senaryo; sınır durumlar dâhil doğru yönlendirme |
| Otomatik test kümesi | 94/94 (veri katmanı, araç yönlendirme, çağrı API'si, kanal köprüsü, dil işleme) |
| Konuşma tanıma | Alan sözlüğü ve kayıt başlangıcı iyileştirmeleriyle alan terimlerinde doğru çözüm; sözel sayıların ("bin iki yüz elli lira") doğru tutara bağlanması |
| Güvenlik müdahalesi | Tam kart numarası okunma girişiminde sözün kesilmesi doğrulandı |
| Geri kazanım (temsilî) | Tek proaktif aramada 143.333 TL/ay hacim kaydı; panelde parasal izleme |

**Tablo 2:** Kavram doğrulama bulguları.

### 6.3 Tartışma

2–3 saniyelik tur süresi, telefon görüşmesinin doğal akışını bozmayan bir
etkileşim hızına işaret etmekte; sektörün saniye altı gecikme hedefi [12, 13]
ile arasındaki fark, yol haritasındaki akış tabanlı (streaming) tanıma ve sentez
adımıyla kapatılabilir görünmektedir. Çalışma; kelime başı kırpılmasının
transkript hatalarına yol açabildiğini (kayıt başlatma stratejisiyle
giderilmiştir), model çıktısındaki sayısal biçim çeşitliliğinin tür dönüşümü
gerektirdiğini ve ses önizlemesi ile gürültü kalibrasyonunun etkileşimini
ortaya koymuş; her üç bulgu da mimariye kalıcı düzeltme olarak işlenmiştir.
Sentetik veri kümesi bir sınırlılıktır; araç arayüzleri gerçek servislerle bire
bir eşleşecek biçimde tanımlandığından üretim entegrasyonunun kapsamı sınırlı
tutulabilecektir.

## 7. Risk Analizi

| Risk | Azaltım önlemi |
| --- | --- |
| Konuşma tanıma hataları (aksan, gürültü, finansal terimler) | Alan sözlüğü istemi; eşikte anlık kayıt başlatma; regex tabanlı anlamsal yedek; tutar teyidi diyaloğu |
| Model uydurması (tutar/tarih üretimi) | Veriye dayalı yanıt ilkesi; araç dışı sayısal bilgi üretiminin istem ve son-işlemle engellenmesi; araç rozetiyle denetlenebilirlik |
| Harici API bağımlılığı (kota, kesinti) | Yedek anahtar, yerel STT/LLM yedekleri, araç hatasında zarif bozulma |
| Kimlik güvenliği (hat kimliği varsayımı) | Hassas işlemlerin asistana kapalı tutulması; yol haritasında ses biyometrisi / tek kullanımlık kod |
| Telefon şebekesi entegrasyonu | Taşıma katmanından bağımsız çağrı API'si; SIP/PSTN geçişinin yalnızca istemciyi etkilemesi |
| Karmaşık/duygusal görüşmeler | Ölçülü otonomi: özetli insan devri, SLA sayaçlı kuyruk |
| Mevzuat (KVKK/GDPR, EU AI Act şeffaflık) | Kart verisi reddi, anahtarların ortam değişkeninde tutulması, yapay zekâ kimliğinin beyanı |

**Tablo 3:** Risk analizi ve karşılık gelen azaltım önlemleri.

## 8. Pazar Analizi, Etki ve Sürdürülebilirlik

### 8.1 Pazar büyüklüğü ve konumlanma

Konuşma tabanlı yapay zekâ pazarının 2025'te 14,79 milyar USD'den 2034'te 82,46
milyar USD'ye ulaşması beklenmektedir [15]; sesli yapay zekâ ajanları alt
segmenti %39 yıllık bileşik büyümeyle 2033'te 35,24 milyar USD'ye ulaşacaktır
[16]. Türkiye pazarında 1,6 milyar USD seviyesindeki hacmin 2030'da 7 milyar
USD'yi aşması öngörülmektedir [9]. Sektörel dağılımda bankacılık-finans-sigorta
%32,9'luk payla en büyük dikeyi oluşturmaktadır [16, 17]; bu tablo, ödeme
alanına odaklanan önerilen sistemin en büyük pazar diliminde konumlandığını
göstermektedir. Türkiye'de proaktif yapay zekâ temelli müşteri etkileşimlerinin
yalnızca %8 düzeyinde olması [18], sistemin giden arama katmanının büyük ölçüde
karşılanmamış bir alana denk düştüğüne işaret etmektedir.

Otomasyonun ekonomik gerekçesi güçlüdür: yapay zekâ ile yürütülen bir çağrının
maliyeti yaklaşık 0,30–0,50 USD iken insan temsilciyle yürütülen çağrı 6–12 USD
seviyesindedir [17]; Gartner konuşma tabanlı yapay zekânın 2026'da çağrı merkezi
iş gücü maliyetlerinde 80 milyar USD tasarruf sağlayacağını öngörmektedir [17].
İyi yapılandırılmış dağıtımlarda rutin gelen çağrıların %30–60'ı insan
müdahalesi olmadan çözülebilmektedir [17]; sesli yapay zekâ kullanan
işletmelerde üç yıllık yatırım getirisi %331–391 aralığında raporlanmıştır [14].

### 8.2 Gelir modeli ve etki

Sistem öncelikle kurum içi platform olarak konumlanır: değer, destek
maliyetinin düşmesi, kaçan çağrı kaynaklı hacim kaybının önlenmesi ve geri
kazanım/çapraz satış gelirleriyle ölçülür. Orta vadede aynı çekirdeğin, araç
kümesi değiştirilerek diğer dikeylere (sigorta, e-ticaret, telekom) beyaz
etiketli hizmet (AIaaS) olarak sunulması mümkündür. Etki tarafında; bekleme
stresini ortadan kaldıran anında yanıtların müşteri memnuniyetini yükseltmesi,
rutin görevlerden kurtulan temsilcilerin yüksek katma değerli vakalara
odaklanması ve kurtarılan hacim metriğiyle gelir etkisinin şeffaf raporlanması
öngörülmektedir.

## 9. Sonuç ve Gelecek Çalışmalar

Bu çalışmada, ödeme kuruluşlarının üye işyeri destek hattını dönüştüren, araç
çağırma mimarisine dayalı, veriye bağlı ve gelir odaklı bir sesli yapay zekâ
asistanı sunulmuştur. Kavram doğrulama bulguları (2–3 saniyelik uçtan uca tur,
7/7 senaryo başarımı, 94 maddelik otomatik doğrulama, parasal izlenebilir geri
kazanım) bir arada değerlendirildiğinde, önerilen sistemin çağrı otomasyonu
alanında uygulanabilir ve ayırt edici bir çözüm olduğu görülmektedir.

Gelecek çalışmalar dört eksende planlanmaktadır: (i) SIP/PSTN entegrasyonuyla
gerçek telefon şebekesine geçiş; (ii) ses biyometrisi veya tek kullanımlık kod
ile kimlik doğrulamanın güçlendirilmesi; (iii) akış tabanlı tanıma ve sentezle
algılanan gecikmenin saniye altına indirilmesi; (iv) araç arayüzleriyle bire bir
eşleşen gerçek ödeme altyapısı servislerine bağlanılması ve daha geniş ölçekli,
gerçek kullanıcılı pilot uygulamaların yürütülmesi.

## Kaynakça

[1] PwC (2023). *Next-Generation Customer Experience Management Research*.
[2] Frost & Sullivan (2018). *Global Contact Center Customer Experience Management Market*, Şubat 2018.
[3] Deloitte (2019). *2019 Global Contact Center Survey*, Eylül 2019.
[4] McKinsey & Company (2020). *Transforming Customer Service Operations with AI*, Haziran 2020.
[5] IBM Institute for Business Value (2017). *Chatbots and Cognitive Engagement*, Aralık 2017.
[6] Segment (2017). *The 2017 State of Personalization Report*.
[7] Walker Information. *Customers 2020: A Progress Report*.
[8] MarketsandMarkets (2024). *AI in Customer Service Market — Forecast to 2030*.
[9] Statista (2024). *Artificial Intelligence Adoption in Turkey and MENA*.
[10] International Journal of Human-Computer Studies (2023). *Empathetic Response Systems and Customer Satisfaction*.
[11] European Journal of Law and Technology (2022). *GDPR Compliance in Conversational AI Platforms*.
[12] IntelEvo Research (2026). *Global AI Voice Agent Market Size & Forecast 2034*.
[13] VoiceAIWrapper (2026). *Voice AI Market Trends & Growth 2026: Segments, Funding, Forecasts*.
[14] Ringly (2026). *47 Voice AI Statistics for 2026: Market Size, Growth, and Trends*.
[15] Fortune Business Insights (2026). *Conversational AI Market Size, Share & Industry Analysis, 2026–2034*.
[16] Grand View Research (2026). *AI Voice Agents Market Size and Share Report, 2026–2033*.
[17] RaftLabs (2026). *Voice AI Statistics: Market Size, Adoption, and ROI Data*.
[18] Müşteri Deneyimi Yönetimi ve Teknolojileri Derneği — MDYD (2024). *Yeni Nesil Müşteri Deneyimi Yönetimi Araştırması 2024*. Aktaran: Marketing Türkiye.
