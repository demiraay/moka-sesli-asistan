"""Gosterim (demo) test profili.

Panelden duzenlenebilen TEK bir isletme kaydi: sunumda kendi adinizi/isletmenizi
yazip asistanla o kimlik uzerinden konusabilirsiniz.

NEDEN VERI DE URETILIYOR: bos bir profil demoyu bozar — asistan "kayit
bulunamadi" der. Bu yuzden profil her kaydedildiginde aylik cirodan TUTARLI bir
islem/hakedis/cihaz kumesi TURETILIR:

    aylik ciro  ->  gunluk ciro  ->  gunluk hakedis partisi  ->  o partinin islemleri

Uretilen tutarlar birbirini tutar (brut = islemler toplami, komisyon = plan
orani, net = brut - komisyon), boylece asistanin soyledigi her rakam denetlenebilir.
"""

from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from core import db

TEST_MERCHANT_ID = "M-TEST"
TEST_TERMINAL_ID = "TRM-TEST"

# Uretim deterministik ama profile OZGU: tohum profilin kendi verisinden
# (isletme adi + telefon) turer, boylece farkli profiller farkli cesitlilik
# uretir ama ayni profil hep ayni veriyi verir. builtin hash() KULLANILMAZ —
# PYTHONHASHSEED ile surec-basina tuzlanir ve cross-run kararsiz olurdu.
_SEED = 20260720

# Cihaz firmware havuzu (seed vokabuleriyle ayni) — sabit "2.4.1" yerine cesitli.
_FIRMWARE_POOL = ("1.9.3", "2.1.4", "2.2.0", "2.4.1", "3.1.0")


def _stable_seed(profile: Dict[str, Any]) -> int:
    """Profile ozgu, surec-bagimsiz kararli tohum (cross-run ayni)."""
    key = f"{profile.get('business_name', '')}|{profile.get('phone', '')}"
    return _SEED ^ int(hashlib.md5(key.encode("utf-8")).hexdigest()[:8], 16)


def _channels_from_products(products: List[str]) -> List[str]:
    """Urunlere gore islem kanali havuzu; fiziksel POS agirlikli.

    Sabit "pos" yerine profilin urun karmasindan cesitlilik: sanal POS veya
    odeme linki varsa ara sira o kanallar da gorunur.
    """
    channels: List[str] = []
    for product in products:
        if product == "fiziksel_pos":
            channels += ["pos", "pos", "pos"]      # agirlikli
        elif product == "sanal_pos":
            channels.append("sanal_pos")
        elif product == "odeme_linki":
            channels.append("odeme_linki")
    return channels or ["pos"]

DEFAULT_PROFILE: Dict[str, Any] = {
    "business_name": "Demo Kuruyemiş",
    "owner_name": "Ahmet Demir",
    "salutation": "Ahmet Bey",
    "sector": "Gıda Perakende",
    "mcc": "5499",
    "city": "İstanbul",
    "district": "Kadıköy",
    "phone": "+905550000001",
    "email": "demo@ornek.com",
    "commission_plan_id": "PLAN-STD",
    "iban_masked": "TR** **** **** **** **99 01",
    "notes": "Panelden düzenlenebilen gösterim profili.",
    "products": ["fiziksel_pos", "sanal_pos"],
    "device_model": "Moka P20",
    "device_status": "aktif",
    "device_note": "",
    # Eskiden yeniye 6 aylik ciro; son eleman icinde bulunulan aydir.
    "volumes": [78000, 92000, 105000, 118000, 134000, 152000],
}

EDITABLE_TEXT_FIELDS = (
    "business_name", "owner_name", "salutation", "sector", "city", "district",
    "phone", "email", "iban_masked", "notes", "device_model", "device_note",
)


def _months(count: int, today: Optional[date] = None) -> List[str]:
    """Son `count` ayin etiketi, eskiden yeniye; sonuncusu icinde bulunulan ay."""
    base = today or date.today()
    labels = []
    for offset in range(count - 1, -1, -1):
        year, month = base.year, base.month - offset
        while month <= 0:
            month += 12
            year -= 1
        labels.append(f"{year:04d}-{month:02d}")
    return labels


def read_profile(repo) -> Dict[str, Any]:
    """Kayitli test profilini dondurur; yoksa varsayilani."""
    merchant = repo.get_merchant(TEST_MERCHANT_ID)
    if not merchant:
        return dict(DEFAULT_PROFILE)

    devices = merchant.get("devices") or []
    device = devices[0] if devices else {}
    profile = {key: merchant.get(key, "") for key in
               ("business_name", "owner_name", "salutation", "sector", "mcc",
                "city", "district", "phone", "email", "commission_plan_id",
                "iban_masked", "notes")}
    profile["products"] = merchant.get("products") or []
    profile["device_model"] = device.get("model", "")
    profile["device_status"] = device.get("status", "aktif")
    profile["device_note"] = device.get("note") or ""
    profile["volumes"] = [entry.get("volume", 0)
                          for entry in merchant.get("monthly_volume_try", [])]
    return profile


def save_profile(repo, submitted: Dict[str, Any]) -> Dict[str, Any]:
    """Test profilini yazar ve turetilmis veriyi yeniden uretir.

    Donen sozluk kaydedilen (normalize edilmis) profildir.
    """
    profile = dict(DEFAULT_PROFILE)
    profile.update({key: str(submitted.get(key, profile.get(key, "")) or "").strip()
                    for key in EDITABLE_TEXT_FIELDS})

    plan_id = str(submitted.get("commission_plan_id") or "").strip()
    profile["commission_plan_id"] = plan_id if repo.get_plan(plan_id) else "PLAN-STD"

    status = str(submitted.get("device_status") or "").strip()
    profile["device_status"] = status if status in ("aktif", "pasif") else "aktif"

    products = submitted.get("products")
    if isinstance(products, str):
        products = [products]
    profile["products"] = [p for p in (products or [])
                           if p in ("fiziksel_pos", "sanal_pos", "odeme_linki")] or ["fiziksel_pos"]

    profile["volumes"] = _clean_volumes(submitted.get("volumes"),
                                        fallback=DEFAULT_PROFILE["volumes"])

    if not profile["business_name"]:
        profile["business_name"] = DEFAULT_PROFILE["business_name"]
    if not profile["owner_name"]:
        profile["owner_name"] = DEFAULT_PROFILE["owner_name"]
    if not profile["salutation"]:
        profile["salutation"] = profile["owner_name"].split(" ")[0] + " Bey"

    _write(repo, profile)
    return profile


def _clean_volumes(raw: Any, fallback: List[int]) -> List[int]:
    values: List[int] = []
    for index, item in enumerate(raw or []):
        try:
            values.append(max(0, int(float(str(item).replace(".", "").replace(",", ".")))))
        except (TypeError, ValueError):
            values.append(fallback[index] if index < len(fallback) else 0)
    while len(values) < len(fallback):
        values.append(fallback[len(values)])
    return values[:len(fallback)]


def ensure_exists(repo) -> bool:
    """Test profili yoksa varsayilanla olusturur. True = olusturuldu."""
    if repo.get_merchant(TEST_MERCHANT_ID):
        return False
    save_profile(repo, dict(DEFAULT_PROFILE))
    return True


# --------------------------------------------------------------- yazma

def _write(repo, profile: Dict[str, Any]) -> None:
    today = date.today()
    plan = repo.get_plan(profile["commission_plan_id"]) or {}
    rate = float(plan.get("rate_pct") or 0)

    # Tek deterministik RNG akisi: cihaz + islem/hakedis verisinin TAMAMI bundan
    # turer. Profile ozgu tohum -> ayni profil ayni veri, farkli profil farkli.
    rng = random.Random(_stable_seed(profile))

    from core.phone_utils import normalize_phone_number

    with db.session(repo.db_path) as connection:
        # Turetilmis veri her kayitta bastan uretilir: profil degisince
        # rakamlar tutarsiz kalmasin.
        connection.execute("DELETE FROM transactions WHERE merchant_id = ?", (TEST_MERCHANT_ID,))
        connection.execute("DELETE FROM settlements WHERE merchant_id = ?", (TEST_MERCHANT_ID,))
        connection.execute("DELETE FROM pos_devices WHERE merchant_id = ?", (TEST_MERCHANT_ID,))
        connection.execute("DELETE FROM merchant_products WHERE merchant_id = ?", (TEST_MERCHANT_ID,))
        connection.execute("DELETE FROM merchant_monthly_volume WHERE merchant_id = ?", (TEST_MERCHANT_ID,))

        connection.execute(
            """
            INSERT INTO merchants (merchant_id, business_name, owner_name, salutation,
                sector, mcc, city, district, phone, phone_e164, email,
                commission_plan_id, iban_masked, status, joined, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(merchant_id) DO UPDATE SET
                business_name=excluded.business_name, owner_name=excluded.owner_name,
                salutation=excluded.salutation, sector=excluded.sector, mcc=excluded.mcc,
                city=excluded.city, district=excluded.district, phone=excluded.phone,
                phone_e164=excluded.phone_e164, email=excluded.email,
                commission_plan_id=excluded.commission_plan_id,
                iban_masked=excluded.iban_masked, notes=excluded.notes
            """,
            (TEST_MERCHANT_ID, profile["business_name"], profile["owner_name"],
             profile["salutation"], profile["sector"], profile.get("mcc", ""),
             profile["city"], profile["district"], profile["phone"],
             normalize_phone_number(profile["phone"]), profile["email"],
             profile["commission_plan_id"], profile["iban_masked"], "active",
             today.isoformat(), profile["notes"]),
        )

        for order, product in enumerate(profile["products"]):
            connection.execute(
                "INSERT INTO merchant_products (merchant_id, product_key, sort_order) "
                "VALUES (?,?,?)", (TEST_MERCHANT_ID, product, order))

        month_labels = _months(len(profile["volumes"]), today)
        for index, volume in enumerate(profile["volumes"]):
            offset = index - (len(profile["volumes"]) - 1)
            connection.execute(
                "INSERT INTO merchant_monthly_volume "
                "(merchant_id, month_offset, month, volume_try) VALUES (?,?,?,?)",
                (TEST_MERCHANT_ID, offset, month_labels[index], volume))

        # Firmware sabit "2.4.1" degil, havuzdan; last_seen bugun ama saat cesitli.
        firmware = rng.choice(_FIRMWARE_POOL)
        seen_hour, seen_min = rng.randint(7, 11), rng.randint(0, 59)
        seen_token = f"D0T{seen_hour:02d}:{seen_min:02d}:00"
        connection.execute(
            "INSERT INTO pos_devices (terminal_id, merchant_id, model, status, firmware, "
            "last_seen_token, last_seen_at, note) VALUES (?,?,?,?,?,?,?,?)",
            (TEST_TERMINAL_ID, TEST_MERCHANT_ID, profile["device_model"],
             profile["device_status"], firmware, seen_token,
             f"{today.isoformat()}T{seen_hour:02d}:{seen_min:02d}:00", profile["device_note"]))

        _write_transactional_data(connection, profile, rate, today, rng)

        connection.execute(
            "INSERT INTO identities (identity, kind, merchant_id, created_at) "
            "VALUES (?,?,?,?) ON CONFLICT(identity) DO UPDATE SET merchant_id=excluded.merchant_id",
            (normalize_phone_number(profile["phone"]), "phone", TEST_MERCHANT_ID,
             datetime.now().isoformat()))


def _write_transactional_data(connection, profile: Dict[str, Any], rate: float,
                              today: date, rng: random.Random) -> None:
    """Aylik cirodan gunluk hakedis partileri ve islemleri turetir.

    Sabitler yerine profilin kendi verisinden CESITLILIK uretilir: hakedis gun
    sayisi, gun-basi islem adedi, islem saatleri, odeme saati, kanal ve durum
    hepsi rng ile degisir — ama finansal invaryantlar korunur (brut = islemler
    toplami, komisyon = plan orani, net = brut - komisyon) ve day0 partisi hep
    "planlandi" (bekleyen hakedis senaryosu).
    """
    monthly = profile["volumes"][-1] if profile["volumes"] else 0
    daily = max(monthly / 30.0, 0.0)

    # Hakedis gun sayisi 4..7 (ust sinir 7: demo icin hakedis 0..7 gun icinde
    # olmali). Kanal havuzu profilin urunlerinden turer.
    settlement_days = min(7, 4 + rng.randint(0, 3))
    channels = _channels_from_products(profile.get("products") or [])

    # En yeni parti BUGUN'un satislaridir ve YARIN odenir (T+1 kurali). Boylece
    # "param ne zaman yatacak" cevabi her zaman GELECEKTE kalir.
    for day_offset in range(settlement_days - 1, -1, -1):
        batch_day = today - timedelta(days=day_offset)
        batch_id = f"SET-T{day_offset:02d}"

        # Gun-basi islem adedi degisken (2..6): sabit 4 yerine dogal cesitlilik.
        txn_per_day = max(2, 4 + rng.randint(-1, 2))
        weights = [rng.uniform(0.7, 1.3) for _ in range(txn_per_day)]
        total_weight = sum(weights) or 1.0
        amounts = [round(daily * weight / total_weight, 2) for weight in weights]
        gross = round(sum(amounts), 2)
        commission = round(gross * rate / 100, 2)

        # En yeni parti henuz odenmemis: planlanmis hakedis her zaman bulunsun.
        status = "planlandı" if day_offset == 0 else "ödendi"
        payout_day = batch_day + timedelta(days=1)
        payout_hour = rng.randint(9, 17)      # sabit 10:00 degil

        connection.execute(
            "INSERT INTO settlements (batch_id, merchant_id, batch_date_token, batch_date, "
            "payout_eta_token, payout_eta, gross_try, commission_try, net_try, status, "
            "iban_masked, txn_count, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (batch_id, TEST_MERCHANT_ID,
             f"D-{day_offset}" if day_offset else "D0", batch_day.isoformat(),
             (f"D-{day_offset - 1}T{payout_hour:02d}:00:00" if day_offset
              else f"D+1T{payout_hour:02d}:00:00"),
             f"{payout_day.isoformat()}T{payout_hour:02d}:00:00",
             gross, commission, round(gross - commission, 2), status,
             profile["iban_masked"], len(amounts), None))

        for index, amount in enumerate(amounts):
            last4 = f"{rng.randint(1000, 9999)}"
            hour = rng.randint(8, 21)         # is saatlerine yayilmis (sabit 9+3i degil)
            minute = rng.randint(0, 59)
            stamp = f"{batch_day.isoformat()}T{hour:02d}:{minute:02d}:00"
            channel = rng.choice(channels)    # sabit "pos" degil, urun karmasindan
            # Cogu onaylandi, ara sira iade/iptal — brut TUM islemleri topladigi
            # icin finansal invaryant bozulmaz.
            roll = rng.random()
            txn_status = "iade" if roll < 0.04 else ("iptal" if roll < 0.07 else "onaylandı")
            txn_commission = round(amount * rate / 100, 2)
            connection.execute(
                "INSERT INTO transactions (txn_id, merchant_id, terminal_id, "
                "settlement_batch_id, channel, amount_try, commission_try, net_try, "
                "card_masked, card_last4, ts_token, ts, ts_day, status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"TXN-T{day_offset:02d}{index}", TEST_MERCHANT_ID, TEST_TERMINAL_ID,
                 batch_id, channel, amount, txn_commission,
                 round(amount - txn_commission, 2),
                 f"**** **** **** {last4}", last4,
                 (f"D-{day_offset}T{hour:02d}:{minute:02d}:00" if day_offset
                  else f"D0T{hour:02d}:{minute:02d}:00"), stamp, batch_day.isoformat(),
                 txn_status))
