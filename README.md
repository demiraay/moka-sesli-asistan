# Moka Sesli Asistan

Ödeme kuruluşlarının işletme müşterilerine telefon üzerinden hizmet veren,
konuşma tabanlı bir yapay zeka destek sistemi. Bu çalışma, Moka United
FinTech Hackathon ("Hack the Idea") kapsamında geliştirilmiş bir prototiptir.

## 1. Problem Tanımı

Ödeme sektöründe üye işyeri destek hatlarına gelen çağrıların önemli bir
bölümü, mevcut işlem verisiyle kısa sürede yanıtlanabilecek rutin
sorgulardan oluşmaktadır: hakediş ödeme zamanı, belirli bir işlemin durumu,
cihaz arızaları ve komisyon kesintileri. Buna karşın arayan işletme sahibi,
IVR menüleri ve bekleme kuyrukları nedeniyle basit bir soru için dakikalarca
zaman kaybedebilmektedir. Bu durum hem operasyon maliyetini yükseltmekte hem
de müşteri kaybını hızlandırmaktadır.

Bu prototip, söz konusu çağrıları doğal dilde karşılayan, yanıtlarını
gerçek işlem verisine dayandıran ve gerektiğinde görüşmeyi bağlamıyla
birlikte insan temsilciye devreden bir sesli asistan (Ada) önermektedir.
Sistem yalnızca bir destek otomasyonu olarak değil, aynı zamanda bir gelir
kanalı olarak tasarlanmıştır: çözülen her çağrıda satış fırsatı denetlenir,
işlem hacmi düşen işletmeler tespit edilerek asistan tarafından proaktif
olarak aranır ve kabul edilen teklifler panele parasal karşılığıyla işlenir.

## 2. Sistem Mimarisi

Sistem üç ana katmandan oluşur: tarayıcı tabanlı çağrı istemcisi, çağrı
API'si ve araç çağırma (tool-calling) mimarisine dayalı diyalog çekirdeği.

```
Arayan işletme
   └─ Çağrı istemcisi (/call): ses etkinliği algılama (VAD), söz kesme
      desteği (barge-in), canlı transkript
        └─ Çağrı API'si (Flask)
             ├─ Konuşma tanıma: Whisper large-v3-turbo (Groq API);
             │  yerel Whisper ile yedekleme
             ├─ Diyalog çekirdeği (AgentOrchestrator)
             │    ├─ Yönlendirici model: araç ve argüman seçimi,
             │    │  müşteri kartı güncellemesi (JSON)
             │    ├─ Dokuz alan aracı → işlem/hakediş/cihaz veri katmanı
             │    └─ Yanıt modeli: konuşmaya uygun Türkçe üretim
             └─ Ses sentezi: ElevenLabs (düşük gecikmeli model)

Yönetim paneli (/admin) ── SQLite ── operasyon ve gelir metrikleri
WhatsApp köprüsü (ikincil kanal) ── aynı diyalog çekirdeği
```

Arayanın kimliği, gerçek çağrı merkezlerindeki CTI yaklaşımına benzer
biçimde hat üzerinden belirlenir; asistan kimlik bilgisini yeniden sormaz.
Ölçümlerde uçtan uca tur süresi (konuşma tanıma, iki model çağrısı ve ses
sentezi dahil) ortalama 2-3 saniye aralığındadır.

### 2.1 Araç Kümesi

| Araç | İşlev |
| --- | --- |
| get_settlement_status | Hakediş sorgusu (son/bekleyen/haftalık) |
| find_transaction | Tutar, tarih veya kart son dört hanesiyle işlem arama |
| troubleshoot_pos | Bilgi bankası destekli arıza giderme; çözümsüzlükte servis kaydı |
| explain_fees | Komisyon planı ve kesinti açıklaması |
| send_statement | Dönem ekstresinin kayıtlı e-postaya gönderimi |
| create_payment_link | Ödeme linki oluşturma |
| recommend_offer | Plan yükseltme, çapraz satış ve geri kazanım teklifleri |
| trigger_handoff | Görüşme özetiyle insan temsilciye devir |
| answer_general | Genel bilgilendirme ve güvenlik uyarıları |

### 2.2 Veri Katmanı

Prototip, gerçek ödeme altyapısını temsil eden bir örnek veri kümesiyle
çalışır: 18 üye işyeri, işlem ve hakediş kayıtları, POS cihaz envanteri,
komisyon planları ve arıza bilgi bankası. Kayıtlardaki tarihler göreli
belirteçlerle tutulur ve yükleme sırasında güncel tarihe çözülür; böylece
gösterim verisi zamanla geçerliliğini yitirmez. Araçların her biri gerçek
sistemde tek bir servis uç noktasına karşılık gelecek şekilde tanımlanmıştır.

## 3. Yönetim Paneli

Panel, destek operasyonunu ve asistanın ürettiği parasal etkiyi tek ekranda
izlemek üzere tasarlanmıştır. Başlıca bileşenler:

- Günlük çağrı sayısı, insan devrine gerek kalmadan çözülen görüşme oranı
  ve temsilci devir sayısı
- Kabul edilen geri kazanım tekliflerinin aylık hacim karşılığı
  ("kurtarılan hacim") ve oluşturulan ödeme linkleri
- İşlem hacmi düşen işletmelerin listesi ve panelden tek adımla
  başlatılabilen proaktif arama akışı
- Bekleyen devirler için SLA sayaçlı kuyruk, görüşme dökümleri ve
  otomatik görev listesi

## 4. Güvenlik Hususları

- Arayan tam kart numarası okumaya başlarsa asistan konuşmayı keserek
  yalnızca son dört hanenin yeterli olduğunu belirtir.
- Yanıtlardaki tüm tutar ve tarih bilgileri araç katmanından gelir; model
  çıktılarında bu alanların uydurulmasına izin verilmez ve arayüzde her
  yanıtın hangi araçla üretildiği izlenebilir.
- Panel, parola tanımlandığında HTTP Basic Auth ile korunur; WhatsApp
  köprüsü istek imzalama için ayrı bir jetonla kilitlenebilir.
- API anahtarları depoya dahil edilmez; ortam değişkenleriyle sağlanır.

## 5. Kurulum

Gereksinimler: Python 3.12+, bir Groq API anahtarı (ücretsiz katman
yeterlidir) ve bir ElevenLabs API anahtarı.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

`.env` dosyasında doldurulması gereken alanlar:

| Değişken | Açıklama |
| --- | --- |
| GROQ_API_KEY | Dil modeli ve konuşma tanıma erişimi |
| ELEVENLABS_API_KEY | Ses sentezi erişimi |
| ELEVENLABS_VOICE_ID | Varsayılan ses (yönetim panelinden değiştirilebilir) |
| GROQ_API_KEY_FALLBACK | İsteğe bağlı yedek anahtar |

## 6. Çalıştırma

```bash
.venv/bin/python server.py
```

Komut, yönetim panelini (5050) ve WhatsApp köprüsünü (5051) başlatır; Node
bağımlılıkları kuruluysa WhatsApp istemcisi de devreye alınır, değilse
atlanır. Erişim noktaları:

| Adres | İçerik |
| --- | --- |
| http://127.0.0.1:5050/call | Sesli görüşme istemcisi |
| http://127.0.0.1:5050/admin | Yönetim paneli |
| http://127.0.0.1:5050/admin/outbound | Proaktif arama listesi |

Gösterim öncesi veritabanını örnek kayıtlarla sıfırlamak için:

```bash
.venv/bin/python scripts/reset_demo.py --seed
```

## 7. Testler

```bash
.venv/bin/python -m pytest tests/
```

Test kümesi 94 adettir ve veri katmanını, senaryo bazlı araç yönlendirmesini,
çağrı API'sini, WhatsApp köprüsünü ve dil işleme katmanını deterministik
olarak (model çağrıları taklit edilerek) doğrular.

## 8. Sınırlılıklar ve Gelecek Çalışmalar

- Çağrı istemcisi tarayıcı üzerinde çalışmaktadır; üretim ortamı için
  SIP/PSTN entegrasyonu (ör. telekom operatörü santral bağlantısı)
  planlanmaktadır. Çağrı API'si taşıma katmanından bağımsız tasarlandığı
  için bu geçiş yalnızca istemci tarafını etkiler.
- Kimlik doğrulama prototipte hat kimliğine dayanır; üretimde ses
  biyometrisi veya tek kullanımlık kod ile güçlendirilmesi gerekir.
- Konuşma akışı sıra tabanlıdır; algılanan gecikmeyi düşürmek için
  akış (streaming) tabanlı tanıma ve sentez değerlendirilmektedir.
- Veri katmanı örnek kayıtlarla çalışmaktadır; araç arayüzleri gerçek
  servislerle bire bir eşleşecek biçimde tanımlanmıştır.
