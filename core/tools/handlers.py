"""Alan araclari — her biri tek @tool bildirimi.

Her handler IKI sey uretir:
  (a) ctx.builder'a yapisal veri  -> panel/DB sozlesmesi (context_json) degismez
  (b) return ile KISA ozet        -> agent loop'ta role="tool" mesaji olur

(b)'nin kisa olmasi onemli: her iterasyonda modele geri gider.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict

from core.formatting import (
    format_try_amount,
    mask_email,
    speakable_iban,
    time_of,
)
from core.mailer import Mailer
from core.merchant_data import describe_day
from core.tools.context import ToolContext
from core.tools.registry import PURE, SIDE_EFFECT, TERMINAL, tool

# Tek gonderici: .env okunur, yapilandirilmamissa sessizce devre disi.
_MAILER = Mailer()


# --------------------------------------------------------------- hakedis

@tool(
    name="get_settlement_status",
    description="Hakedis/odeme durumu: para ne zaman yatacak, ne kadar, neden yatmadi.",
    parameters={
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "enum": ["latest", "pending", "last_week"],
                "description": "latest=son parti (varsayilan), pending=henuz odenmemis, last_week=son 7 gun",
            }
        },
    },
    panel_label="hakediş sorgulandı",
)
def get_settlement_status(ctx: ToolContext, args: Dict[str, Any]) -> str:
    period = args.get("period") or "latest"
    rows = ctx.repo.get_settlements_for_period(ctx.merchant_id, period)

    if not rows:
        ctx.builder.add_fact("Bu dönem için hakediş kaydı bulunamadı.")
        return "Bu donem icin hakedis kaydi yok."

    ctx.builder.set_settlement(rows[0])
    ctx.builder.set_settlements(rows[:3])

    for settlement in rows[:2]:
        day = describe_day(settlement.get("payout_eta", ""))
        at = time_of(settlement.get("payout_eta", ""))
        batch_day = describe_day(settlement.get("batch_date", ""))
        net = format_try_amount(settlement.get("net_try", 0))
        gross = format_try_amount(settlement.get("gross_try", 0))
        commission = format_try_amount(settlement.get("commission_try", 0))
        status = settlement.get("status")
        iban = speakable_iban(settlement.get("iban_masked", ""))

        if status == "ödendi":
            ctx.builder.add_fact(
                f"{batch_day} tarihli satışların hakedişi ödendi: brüt {gross}, "
                f"komisyon {commission}, net {net} ({iban} hesabınıza).")
        elif status == "planlandı":
            ctx.builder.add_fact(
                f"{batch_day} tarihli satışların hakedişi: brüt {gross}, komisyon {commission}, "
                f"net {net}. Ödeme {day} saat {at}'de {iban} hesabınıza planlandı.")
        else:
            note = settlement.get("note") or "banka tarafında doğrulama bekleniyor"
            ctx.builder.add_fact(
                f"DİKKAT: {batch_day} tarihli {net} tutarındaki hakediş hâlâ beklemede ({note}). "
                "Dürüstçe kabul et, gecikme için özür dile ve temsilciye eskalasyon öner.")

    first = rows[0]
    return (f"{len(rows)} hakedis kaydi. En son: {first.get('batch_id')}, "
            f"durum {first.get('status')}, net {format_try_amount(first.get('net_try', 0))}, "
            f"odeme {describe_day(first.get('payout_eta', ''))}.")


# ---------------------------------------------------------------- islem

@tool(
    name="find_transaction",
    description="Belirli bir islemi arar: tutar, gun, kartin son 4 hanesi veya durum ile.",
    parameters={
        "type": "object",
        "properties": {
            "amount_try": {"type": "number", "description": "Islem tutari, TL"},
            "date": {"type": "string", "description": "'bugun', 'dun', 'sali' veya YYYY-MM-DD"},
            "card_last4": {"type": "string", "description": "Kartin son 4 hanesi"},
            "status": {"type": "string", "enum": ["onaylandı", "iade", "iptal", "beklemede"]},
        },
    },
    panel_label="işlem arandı",
)
def find_transaction(ctx: ToolContext, args: Dict[str, Any]) -> str:
    amount = args.get("amount_try")
    on_date = args.get("date")

    rows = ctx.repo.find_transactions(
        ctx.merchant_id,
        amount_try=amount,
        on_date=on_date,
        card_last4=args.get("card_last4"),
        status=args.get("status"),
    )

    if not rows:
        ctx.builder.add_fact("Belirtilen kriterlerle işlem bulunamadı.")

        # Aranan tutar TEK BIR ISLEM degil, bir HAKEDIS TOPLAMI olabilir
        # (musteri ekstrede/bildirimde gordugu rakami soyluyordur). Modele
        # bir sonraki adimi soyle: dongu bunun icin var.
        hint = ""
        if amount is not None:
            for settlement in ctx.repo.list_settlements(ctx.merchant_id, limit=10):
                for field in ("net_try", "gross_try"):
                    if abs((settlement.get(field) or 0) - float(amount)) <= 1.0:
                        hint = (f" Bu tutar bir ISLEM degil, {settlement.get('batch_id')} "
                                "HAKEDIS grubunun tutari olabilir — get_settlement_status "
                                "ile bak.")
                        break
                if hint:
                    break

        nearby = ctx.repo.find_transactions(ctx.merchant_id, on_date=on_date, limit=3)
        found_note = ""
        if nearby:
            amounts = ", ".join(format_try_amount(t.get("amount_try", 0)) for t in nearby)
            ctx.builder.add_fact(f"Yakın zamanda şu tutarlarda işlemler var: {amounts}.")
            found_note = f" Yakin islemler: {amounts}."

        # "Sorma, bak" talimati HER iki dalda da gitmeli: model hicbir sey
        # bulunamadiginda musteriyi sorguya cekmeye en meyilli oluyor.
        return (f"Eslesen islem yok.{found_note}{hint}"
                " Musteriye VERI SORMA; once diger araclarla bak.")

    ctx.builder.set_transactions(rows[:3])
    txn = rows[0]
    day = describe_day(txn.get("timestamp", ""))
    at = time_of(txn.get("timestamp", ""))
    ctx.builder.add_fact(
        f"İşlem bulundu: {format_try_amount(txn.get('amount_try', 0))}, {day} saat {at}, "
        f"{txn.get('card_last4')} ile biten kart, durum: {txn.get('status')}.")

    summary = (f"{len(rows)} islem bulundu. Ilki: {format_try_amount(txn.get('amount_try', 0))}, "
               f"{day} {at}, durum {txn.get('status')}.")

    settlement = ctx.repo.get_settlement_for_transaction(txn)
    if settlement:
        pay_day = describe_day(settlement.get("payout_eta", ""))
        pay_time = time_of(settlement.get("payout_eta", ""))
        if settlement.get("status") == "ödendi":
            ctx.builder.add_fact(
                f"Bu işlem {settlement.get('batch_id')} hakediş grubundaydı ve ödendi "
                f"(net {format_try_amount(settlement.get('net_try', 0))}).")
            summary += f" {settlement.get('batch_id')} grubunda odendi."
        else:
            ctx.builder.add_fact(
                f"Para kaybolmadı: işlem {settlement.get('batch_id')} hakediş grubunda; "
                f"ödeme {pay_day} saat {pay_time}'de hesaba geçecek. Müşteriyi rahatlat.")
            summary += f" {settlement.get('batch_id')} grubunda, odeme {pay_day}."
    return summary


# --------------------------------------------------------------- cihaz

@tool(
    name="troubleshoot_pos",
    description="POS/sanal POS arizasi: adimlari tek tek verdirir. Adim denendikten sonra "
                "step_result ile tekrar cagir.",
    parameters={
        "type": "object",
        "properties": {
            "symptom": {"type": "string", "description": "Musterinin tarif ettigi ariza"},
            "terminal_id": {"type": "string"},
            "step_result": {
                "type": "string",
                "enum": ["resolved", "not_resolved"],
                "description": "Adimlar denendikten SONRA sonucu bildir",
            },
        },
        "required": ["symptom"],
    },
    kind=SIDE_EFFECT,
    panel_label="cihaz arızası",
)
def troubleshoot_pos(ctx: ToolContext, args: Dict[str, Any]) -> str:
    symptom = args.get("symptom") or (ctx.user_profile.get("card") or {}).get("issue") or ""
    step_result = args.get("step_result")
    devices = (ctx.merchant or {}).get("devices") or []

    device = None
    if args.get("terminal_id"):
        device = next((d for d in devices if d.get("terminal_id") == args["terminal_id"]), None)
    if device is None and devices:
        device = devices[0]
    if device:
        ctx.builder.set_device(device)

    if step_result == "resolved":
        ctx.builder.add_fact(
            "Sorun giderildi olarak işaretlendi. Kısaca sevindiğini söyle ve başka "
            "ihtiyacı olup olmadığını sor.")
        ctx.user_profile["conversation_focus"] = "resolved"
        return "Ariza cozuldu olarak isaretlendi."

    if step_result == "not_resolved":
        terminal = device.get("terminal_id") if device else "cihaz"
        model = device.get("model", "") if device else ""
        try:
            task_id = ctx.store.create_task(
                title=f"Servis: {terminal} {model} değişimi — {(ctx.merchant or {}).get('business_name')}",
                user_id=ctx.user_id)
            ctx.builder.add_fact(
                f"Denenen adımlar işe yaramadı. Servis kaydı oluşturuldu (görev #{task_id}): "
                "cihaz 2 iş günü içinde yenisiyle değiştirilecek.")
            opened = f"Servis kaydi acildi (#{task_id})."
        except Exception as error:                      # pragma: no cover
            print(f"Service task warning: {error}")
            ctx.builder.add_fact(
                "Denenen adımlar işe yaramadı. Servis kaydı oluşturuldu: cihaz 2 iş günü "
                "içinde değiştirilecek.")
            opened = "Servis kaydi acildi."

        ctx.builder.add_fact(
            "FIRSAT: Cihaz değişene kadar satış kaçırmasın — telefonuna hemen bir ödeme linki "
            "tanımlayabileceğini söyle; müşterileri karttan linkle ödeyebilir. Kabul ederse "
            "link oluşturulacak.")
        ctx.user_profile["pending_offer"] = {"trigger": "pos_out_of_service"}
        ctx.user_profile["conversation_focus"] = "pos_service"
        return opened + " Cihaz degisene kadar odeme linki onerilebilir."

    article = ctx.repo.match_kb(symptom)
    if article:
        steps = article.get("steps", [])
        ctx.builder.set_kb_steps(steps)
        ctx.builder.add_fact(f"Arıza eşleşti: {article.get('title')}.")
        ctx.builder.add_fact(
            "Adımları TEK TEK ver: önce ilk adımı söyle, denemesini iste. Hepsini birden sayma.")
        ctx.user_profile["conversation_focus"] = "pos_troubleshooting"
        return (f"Ariza eslesti: {article.get('title')}. {len(steps)} adim var; "
                f"ilk adim: {steps[0] if steps else '-'}")

    ctx.builder.add_fact(
        "Bilinen arıza kaydı eşleşmedi. Cihazı kapatıp 30 saniye sonra açmasını öner; "
        "düzelmezse servis kaydı açılacağını söyle.")
    return "Bilinen ariza eslesmedi; kapat-ac onerildi."


# ------------------------------------------------------------ komisyon

@tool(
    name="explain_fees",
    description="Komisyon/kesinti/plan aciklamasi: mevcut plan, bu ayin cirosu ve kesilen komisyon.",
    parameters={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "enum": ["commission", "deduction", "plan_details"]}
        },
    },
    panel_label="komisyon açıklandı",
)
def explain_fees(ctx: ToolContext, args: Dict[str, Any]) -> str:
    topic = args.get("topic") or "commission"
    merchant = ctx.merchant or {}
    summary = ctx.repo.monthly_summary(ctx.merchant_id)
    plan = merchant.get("plan") or {}
    ctx.builder.set_plan_info({"plan": plan, "monthly_summary": summary, "topic": topic})

    fee_note = (f", ayda {format_try_amount(plan.get('monthly_fee_try', 0))} sabit ücret"
                if plan.get("monthly_fee_try") else ", sabit ücret yok")
    ctx.builder.add_fact(
        f"Mevcut plan: {plan.get('name')} — işlem başına %{plan.get('rate_pct')} komisyon{fee_note}.")

    # topic artik gercekten kullaniliyor: onceki surumde arguman aliniyor ama
    # govdede HIC okunmuyordu, uc konu da ayni cevabi uretiyordu.
    if topic == "deduction":
        ctx.builder.add_fact(
            "Kesinti mantığı: komisyon her işlemden anında düşülür, hakediş grubuna NET tutar girer. "
            "Ayrıca bir kesinti yapılmaz.")
    elif topic == "plan_details":
        ctx.builder.add_fact(
            f"Plan detayı: {plan.get('description', '')} "
            f"Alt hacim sınırı: {format_try_amount(plan.get('min_monthly_volume_try', 0))}.")

    if summary:
        ctx.builder.add_fact(
            f"Bu ay ({summary.get('month')}): ciro {format_try_amount(summary.get('gross_try', 0))}, "
            f"kesilen komisyon yaklaşık {format_try_amount(summary.get('commission_try', 0))}.")

    result = (f"Plan {plan.get('name')} (%{plan.get('rate_pct')}). "
              f"Bu ay ciro {format_try_amount((summary or {}).get('gross_try', 0))}, "
              f"komisyon {format_try_amount((summary or {}).get('commission_try', 0))}.")

    upgrade = ctx.repo.get_upgrade_candidate(merchant)
    if upgrade:
        trend = merchant.get("volume_trend") or {}
        new_plan = upgrade["plan"]
        saving = format_try_amount(upgrade["monthly_saving_try"])
        ctx.builder.set_offer({
            "trigger": "volume_growth",
            "plan": new_plan,
            "monthly_saving_try": upgrade["monthly_saving_try"],
        })
        ctx.builder.add_fact(
            f"FIRSAT: Ciro son dönemde belirgin büyümüş (%{trend.get('change_pct', 0)}). "
            f"{new_plan.get('name')} planına geçerse komisyon %{new_plan.get('rate_pct')}'e düşer, "
            f"ayda yaklaşık {saving} cebinde kalır. Açıklamayı bitirdikten SONRA bunu tek cümleyle öner.")
        ctx.user_profile["pending_offer"] = {
            "trigger": "volume_growth",
            "plan_id": new_plan.get("plan_id"),
            "monthly_saving_try": upgrade["monthly_saving_try"],
        }
        # Bu FIRSAT fiilen teklifin kendisidir; sayaci burada isaretlemezsek
        # ayni cagrida recommend_offer ikinci bir teklif sunabiliyordu.
        _mark_offer_made(ctx)
        result += f" FIRSAT: {new_plan.get('name')} plani ayda {saving} tasarruf saglar."
    return result


# --------------------------------------------------------------- ekstre

@tool(
    name="send_statement",
    description="Donem ekstresini gonderir. Kanal ZORUNLU: musteri hangisini "
                "istedigini soylemediyse ONCE SOR, bu araci cagirma.",
    parameters={
        "type": "object",
        "properties": {
            "channel": {
                "type": "string",
                "enum": ["email", "sms"],
                "description": "Musterinin ACIKCA sectigi kanal",
            },
            "to_email": {
                "type": "string",
                "description": "Musteri BASKA bir adres soylediyse o adres "
                               "(ornek: 'muhasebeye at, x@y.com'). Bos birakilirsa "
                               "kayitli e-posta kullanilir. ASLA uydurma.",
            },
            "period": {"type": "string", "enum": ["this_month", "last_month"]},
        },
        "required": ["channel"],
    },
    kind=SIDE_EFFECT,
    once_per_call=True,      # gorusme boyu tek ekstre: loop iki kez gondermesin
    panel_label="ekstre gönderildi",
)
def send_statement(ctx: ToolContext, args: Dict[str, Any]) -> str:
    # Kanal, MUSTERININ sectigi seydir. Model bunu bilmeden cagirirsa arac
    # calismaz: aksi halde "hangi kanaldan isterseniz" diye sorarken ekstre
    # coktan gonderilmis oluyordu (canli denemede goruldu).
    channel = args.get("channel")
    if channel not in ("email", "sms"):
        ctx.builder.add_fact(
            "Ekstre HENÜZ GÖNDERİLMEDİ: müşteri hangi kanalı istediğini "
            "belirtmedi. Önce e-posta mı SMS mi olduğunu sor.")
        return ("GONDERILMEDI: kanal belirsiz. Once musteriye e-posta mi SMS mi "
                "istedigini sor, cevabini aldiktan sonra bu araci tekrar cagir.")

    period = args.get("period") or "this_month"
    month = None
    if period == "last_month":
        today = date.today()
        previous = (date(today.year - 1, 12, 1) if today.month == 1
                    else date(today.year, today.month - 1, 1))
        month = previous.strftime("%Y-%m")

    merchant = ctx.merchant or {}
    summary = ctx.repo.monthly_summary(ctx.merchant_id, month=month)

    try:
        ctx.store.enqueue_outbound_message(
            ctx.user_id,
            f"[Ekstre] {merchant.get('business_name')} — {summary.get('month')} dönemi: "
            f"ciro {format_try_amount(summary.get('gross_try', 0))}, "
            f"komisyon {format_try_amount(summary.get('commission_try', 0))}.",
            sender="ai-statement")
    except Exception as error:                          # pragma: no cover
        print(f"Statement outbox warning: {error}")

    period_summary = (f"Ciro {format_try_amount(summary.get('gross_try', 0))}, "
                      f"komisyon {format_try_amount(summary.get('commission_try', 0))}.")

    if channel != "email":
        ctx.builder.add_fact(
            f"{summary.get('month')} dönemi ekstresi kayıtlı telefona kısa mesajla gönderildi.")
        ctx.builder.add_fact(f"Dönem özeti: {period_summary}")
        return f"{summary.get('month')} ekstresi SMS ile gonderildi."

    # --- e-posta: musterinin verdigi adres varsa oraya ---------------------
    address = (args.get("to_email") or merchant.get("email") or "").strip()
    custom = bool(args.get("to_email"))

    # IKI MOD:
    #  - EMAIL_ENABLED=0 (varsayilan): gonderim SIMULE. Islemler, hakedisler ve
    #    odeme linkleri de simule oldugu icin tutarli; "gonderildi" demek burada
    #    prototipin kendi dunyasinda dogrudur.
    #  - EMAIL_ENABLED=1: GERCEK gonderim. Artik gercek bir vaat var, dolayisiyla
    #    basarisizlik gizlenmez — asistan "gonderdim" DEMEZ.
    if not _MAILER.is_configured():
        shown = address if custom else mask_email(address)
        whose = ("müşterinin BELİRTTİĞİ adrese" if custom
                 else "müşterinin KAYITLI e-posta adresine")
        ctx.builder.add_fact(
            f"{summary.get('month')} dönemi ekstresi {whose} gönderildi: {shown}.")
        ctx.builder.add_fact(f"Dönem özeti: {period_summary}")
        return f"{summary.get('month')} ekstresi {shown} adresine gonderildi."

    result = _MAILER.send(
        address,
        subject=f"{merchant.get('business_name')} — {summary.get('month')} dönemi ekstresi",
        body=(f"Sayın {merchant.get('owner_name')},\n\n"
              f"{summary.get('month')} dönemine ait ekstre özetiniz:\n\n"
              f"  Ciro       : {format_try_amount(summary.get('gross_try', 0))}\n"
              f"  Komisyon   : {format_try_amount(summary.get('commission_try', 0))} "
              f"(%{summary.get('rate_pct')})\n"
              f"  Plan       : {summary.get('plan_name')}\n"
              f"  İşlem adedi: {summary.get('txn_count')}\n\n"
              "İyi çalışmalar dileriz.\nMoka United"))

    shown = address if custom else mask_email(address)
    # Musteri BASKA bir adres verdiyse "kayitli adresinize" demek yanlis olur
    # (ornegin muhasebeciye yollatirken). Hangisi oldugunu acikca soyle.
    whose = ("müşterinin BELİRTTİĞİ adrese" if custom
             else "müşterinin KAYITLI e-posta adresine")
    if result.sent:
        ctx.builder.add_fact(
            f"{summary.get('month')} dönemi ekstresi {whose} GÖNDERİLDİ: {shown}.")
        ctx.builder.add_fact(f"Dönem özeti: {period_summary}")
        return f"{summary.get('month')} ekstresi {shown} adresine GONDERILDI."

    # Gonderilemedi: asistan "gonderdim" DEMEMELI.
    print(f"Ekstre e-postasi gonderilemedi: {result.reason}")
    ctx.builder.add_fact(
        f"Ekstre {shown} adresine GÖNDERİLEMEDİ. Gönderildiğini SÖYLEME; "
        "kısaca iletilemediğini belirt ve adresi teyit et ya da başka bir "
        "kanal öner.")
    return (f"GONDERILEMEDI ({result.reason}). Musteriye gonderildigini soyleme; "
            "adresi teyit et ya da SMS oner.")


# ---------------------------------------------------------- odeme linki

@tool(
    name="create_payment_link",
    description="Uzaktan tahsilat icin odeme linki olusturur ve SMS ile gonderir.",
    parameters={
        "type": "object",
        "properties": {
            "amount_try": {"type": "number", "description": "Sabit tutar; bos ise tutar serbest"},
            "description": {"type": "string"},
        },
    },
    kind=SIDE_EFFECT,
    panel_label="ödeme linki oluşturuldu",
)
def create_payment_link(ctx: ToolContext, args: Dict[str, Any]) -> str:
    link = ctx.repo.create_payment_link(
        ctx.merchant_id,
        amount_try=args.get("amount_try"),
        description=args.get("description"))
    ctx.builder.set_payment_link(link)

    amount_note = (f" ({format_try_amount(link['amount_try'])} tutarında)"
                   if link.get("amount_try") else " (tutar serbest)")
    # URL sesli okunmaz: link context'te durur (panel/transkript gosterir),
    # konusmada yalnizca SMS ile gonderildigi soylenir.
    ctx.builder.add_fact(
        f"Ödeme linki oluşturuldu{amount_note} ve telefonuna SMS ile gönderildi. "
        "Müşterileri bu linkten kartla ödeyebilir, tutarlar hakedişe dahil olur. "
        "Linkin adresini SESLİ OKUMA; SMS'e geldiğini söyle.")

    payload = {"url": link["url"], "amount_try": link.get("amount_try"),
               "merchant_id": ctx.merchant_id}
    pending = ctx.user_profile.get("pending_offer") or {}
    if pending.get("trigger") == "pos_out_of_service":
        # POS arizasi sirasindaki linki kabul edilen upsell olarak say.
        payload["trigger"] = "pos_out_of_service"
        _record_lead(ctx, "offer_accepted", payload)
        ctx.user_profile["pending_offer"] = None
        ctx.user_profile["offer_made"] = True
    _record_lead(ctx, "payment_link_created", payload)
    return f"Odeme linki olusturuldu{amount_note} ve SMS ile gonderildi."


# --------------------------------------------------------------- teklif

@tool(
    name="recommend_offer",
    description="GELIR araci: sorun cozuldukten SONRA baglama uygun tek teklif sunar; "
                "musteri kabul ederse accepted=true ile tekrar cagir.",
    parameters={
        "type": "object",
        "properties": {
            "trigger": {
                "type": "string",
                "enum": ["volume_growth", "social_selling", "dormant_retention",
                         "pos_out_of_service"],
            },
            "accepted": {"type": "boolean", "description": "Musteri teklifi kabul etti mi"},
        },
        "required": ["trigger"],
    },
    kind=SIDE_EFFECT,
    panel_label="teklif sunuldu",
)
def recommend_offer(ctx: ToolContext, args: Dict[str, Any]) -> str:
    merchant = ctx.merchant or {}
    pending = ctx.user_profile.get("pending_offer") or {}
    trigger = args.get("trigger") or pending.get("trigger")
    accepted = bool(args.get("accepted"))

    if accepted:
        source = pending or {"trigger": trigger}
        payload = dict(source)
        payload["merchant_id"] = ctx.merchant_id
        if source.get("trigger") == "dormant_retention" or trigger == "dormant_retention":
            series = [entry.get("volume", 0) for entry in merchant.get("monthly_volume_try", [])]
            healthy = sorted(series, reverse=True)[:3]
            recovered = round(sum(healthy) / len(healthy)) if healthy else 0
            payload["recovered_volume_try"] = recovered
            ctx.builder.add_fact(
                f"Teklif kabul edildi ve kaydedildi. Aylık yaklaşık {format_try_amount(recovered)} "
                "hacim geri kazanılıyor. Sıcak bir teşekkür et, planın bugün aktifleştirileceğini söyle.")
        else:
            ctx.builder.add_fact(
                "Teklif kabul edildi ve kaydedildi. Teşekkür et; talebin temsilci onayıyla bugün "
                "aktifleştirileceğini söyle.")
        _record_lead(ctx, "offer_accepted", payload)
        ctx.user_profile["pending_offer"] = None
        ctx.user_profile["offer_made"] = True
        return "Teklif kabul edildi ve kaydedildi."

    if ctx.user_profile.get("offer_made"):
        ctx.builder.add_fact(
            "Bu görüşmede zaten bir teklif sunuldu. İkinci teklif YAPMA; mevcut konuya devam et.")
        return "Bu gorusmede zaten teklif sunuldu; ikinci teklif yapma."

    if trigger == "volume_growth":
        upgrade = ctx.repo.get_upgrade_candidate(merchant)
        if not upgrade:
            ctx.builder.add_fact("Uygun bir üst plan bulunamadı; teklif sunma.")
            return "Uygun ust plan yok; teklif sunulmadi."
        new_plan = upgrade["plan"]
        saving = format_try_amount(upgrade["monthly_saving_try"])
        ctx.builder.set_offer({"trigger": trigger, "plan": new_plan,
                               "monthly_saving_try": upgrade["monthly_saving_try"]})
        ctx.builder.add_fact(
            f"TEKLİF: {new_plan.get('name')} planı — komisyon %{new_plan.get('rate_pct')}, "
            f"ayda yaklaşık {saving} tasarruf. "
            "Tek cümleyle, yardımcı olma tonunda sun ve ister misiniz diye sor.")
        ctx.user_profile["pending_offer"] = {
            "trigger": trigger, "plan_id": new_plan.get("plan_id"),
            "monthly_saving_try": upgrade["monthly_saving_try"]}
        _mark_offer_made(ctx)
        return f"TEKLIF sunuldu: {new_plan.get('name')}, ayda {saving} tasarruf."

    if trigger == "social_selling":
        missing = [key for key in ("sanal_pos", "odeme_linki")
                   if key not in merchant.get("products", [])]
        labels = {"sanal_pos": "Sanal POS", "odeme_linki": "Ödeme Linki"}
        products = ", ".join(labels[key] for key in missing) or "Sanal POS"
        ctx.builder.set_offer({"trigger": trigger, "products": missing})
        ctx.builder.add_fact(
            f"TEKLİF: Instagram/internetten satış için {products} tam çözüm — havale kovalamak "
            "yerine link gönderir ya da siteye entegre eder, kartla tahsil eder. "
            "Başvuru kaydı açmayı öner.")
        ctx.user_profile["pending_offer"] = {"trigger": trigger, "products": missing}
        _mark_offer_made(ctx)
        return f"TEKLIF sunuldu: {products}."

    if trigger == "dormant_retention":
        retention = ctx.repo.get_retention_plan()
        trend = merchant.get("volume_trend") or {}
        if not retention:
            return "Geri kazanim plani tanimli degil."
        ctx.builder.set_offer({"trigger": trigger, "plan": retention})
        ctx.builder.add_fact(
            f"Hacim son dönemde ciddi düşmüş (%{abs(trend.get('change_pct', 0))} azalma). "
            f"TEKLİF: {retention.get('name')} — 3 ay boyunca %{retention.get('rate_pct')} komisyon, "
            "sabit ücret yok. Empatiyle, geri kazanmak istediğinizi belirterek sun.")
        ctx.user_profile["pending_offer"] = {"trigger": trigger,
                                             "plan_id": retention.get("plan_id")}
        _mark_offer_made(ctx)
        return f"TEKLIF sunuldu: {retention.get('name')} geri kazanim plani."

    if trigger == "pos_out_of_service":
        ctx.builder.add_fact(
            "TEKLİF: Cihaz çalışana kadar ödeme linkiyle tahsilata devam edebilir — "
            "isterse hemen link oluşturulacak.")
        ctx.user_profile["pending_offer"] = {"trigger": trigger}
        _mark_offer_made(ctx)
        return "TEKLIF sunuldu: gecici odeme linki."

    ctx.builder.add_fact("Belirgin bir fırsat yok; teklif sunma.")
    return "Belirgin firsat yok."


# ----------------------------------------------------------------- devir

@tool(
    name="trigger_handoff",
    description="Insan temsilciye devret: ofke, dolandiricilik, chargeback, hukuki tehdit, "
                "hesap kapatma, cozulemeyen ariza veya acik temsilci talebi.",
    parameters={
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "Devir gerekcesi, kisa"},
            "missing_info": {"type": "array", "items": {"type": "string"}},
            # Onceki semada BU ALAN YOKTU ama kod ve panel kuyrugu kullaniyordu.
            "share_contact_details": {
                "type": "boolean",
                "description": "Musteri temsilcinin iletisim bilgisini istediyse true",
            },
        },
        "required": ["reason"],
    },
    kind=TERMINAL,
    once_per_call=True,
    panel_label="temsilciye devredildi",
    requires_merchant=False,
)
def trigger_handoff(ctx: ToolContext, args: Dict[str, Any]) -> str:
    reason = args.get("reason") or "Müşteri talebi"
    ctx.builder.trigger_handoff(
        reason=reason,
        missing_info=args.get("missing_info") or [],
        share_contact_details=bool(args.get("share_contact_details")))
    ctx.builder.add_fact(
        "İnsan temsilciye devir tetiklendi. Müşteriyi doğrula (haklısınız de), özetin "
        "temsilciye iletildiğini ve hemen bağlanacağını söyle.")
    ctx.user_profile["handoff_reason"] = reason
    return f"Temsilciye devir tetiklendi ({reason})."


# ----------------------------------------------------------------- genel

@tool(
    name="answer_general",
    description="Selamlama, sirket/urun bilgisi, calisma saatleri, tesekkur ve kart guvenligi uyarisi.",
    parameters={
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["greeting", "company_info", "how_it_works", "working_hours",
                         "security_smalltalk", "thanks", "other"],
            }
        },
    },
    panel_label="genel bilgi",
    requires_merchant=False,
)
def answer_general(ctx: ToolContext, args: Dict[str, Any]) -> str:
    category = args.get("category") or "other"
    details = ctx.config.get_project_details() if ctx.config else {}

    if category == "security_smalltalk":
        ctx.builder.add_fact(
            "GÜVENLİK UYARISI: Müşteri kart numarası paylaşmaya başladı ya da kart verisi "
            "konuşuluyor. Nazikçe ama NET biçimde kes: tam kart numarası asla telefonda "
            "paylaşılmamalı; gerekirse sadece son 4 hane yeterli.")
        ctx.user_profile["conversation_focus"] = "security"
        return "Kart guvenligi uyarisi verilecek."

    if category == "company_info":
        ctx.builder.add_fact(f"Şirket bilgisi: {details.get('description', '')}")
        products = details.get("products", [])
        if products:
            ctx.builder.add_fact(
                "Ürünler: " + "; ".join(f"{p['label']} — {p['description']}" for p in products))
        ctx.user_profile["conversation_focus"] = "company_info"
        return "Sirket/urun bilgisi baglama eklendi."

    if category == "how_it_works":
        payout = (ctx.config.get_payout_rules() if ctx.config else {}) or {}
        cutoff = payout.get("cutoff_local_time", "23:00")
        at = payout.get("payout_time", "10:00")
        ctx.builder.add_fact(
            f"Çalışma bilgisi: gün içi işlemler akşam {cutoff}'te gruplanır, komisyon düşülür, "
            f"ertesi iş günü saat {at}'da IBAN'a yatar (T+1).")
        ctx.user_profile["conversation_focus"] = "how_it_works"
        return "Odeme akisi bilgisi baglama eklendi."

    if category == "working_hours":
        ctx.builder.add_fact(
            f"Destek hattı {details.get('working_hours', '7/24')} açık; Ada her zaman yanıtlıyor.")
        return "Calisma saati bilgisi verildi."

    if category == "thanks":
        ctx.builder.add_fact(
            "Görüşme kapanışı: kısa ve sıcak bir kapanış yap, yeni bir konu açma.")
        ctx.user_profile["conversation_focus"] = "closing"
        return "Kapanis yapilacak."

    ctx.builder.add_fact("Genel sohbet ya da selamlama.")
    if category == "greeting":
        ctx.user_profile["conversation_focus"] = "greeting"
    return "Genel sohbet."


# ------------------------------------------------- operasyon (dahili)

# Bu araclar TUM musteri tabanini gorur. Hattaki bir isletme cagirabilseydi
# rakiplerinin ciro verisini ogrenebilirdi.
#
# DIKKAT: panel "Test Sohbeti" (channel="panel") bir ISLETME SIMULATORUDUR —
# operator orada musteri gibi konusur. Bu yuzden panel DAHILI SAYILMAZ; aksi
# halde demo sirasinda simulasyon ekranindan baska isletmelerin verisi
# sorulabilirdi. Dahili erisim yalnizca ayrilmis operator kimligine acilir.
OPS_CHANNEL = "ops"
OPS_USER_ID = "ops-console"


def _is_internal(ctx: ToolContext) -> bool:
    return ctx.channel == OPS_CHANNEL and ctx.user_id == OPS_USER_ID


@tool(
    name="find_dormant_merchants",
    description="DAHILI: islem hacmi dusen (uyuyan) isletmeleri kayip ciroya gore "
                "siralar. Proaktif arama listesi icin.",
    parameters={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Kac isletme dondurulsun (varsayilan 5)"}
        },
    },
    panel_label="uyuyan işletmeler tarandı",
    requires_merchant=False,
)
def find_dormant_merchants(ctx: ToolContext, args: Dict[str, Any]) -> str:
    if not _is_internal(ctx):
        # Sessizce bos donmek yerine modele NEDEN reddedildigini soyle:
        # boylece musteriye "bu bilgiyi veremem" diyebilir, uydurmaz.
        ctx.builder.add_fact(
            "Bu bilgi yalnızca Moka ekibine açıktır; arayana başka işletmelerin "
            "verisi ASLA söylenmez.")
        return ("REDDEDILDI: bu arac yalnizca dahili panelden kullanilabilir. "
                "Arayana baska isletmelerin verisini verme.")

    limit = max(1, min(int(args.get("limit") or 5), 20))
    dormant = ctx.repo.list_dormant_merchants()[:limit]
    if not dormant:
        ctx.builder.add_fact("Şu anda hacmi ciddi düşen işletme yok.")
        return "Uyuyan isletme yok."

    lines = []
    for merchant in dormant:
        lines.append(
            f"{merchant.get('business_name')} ({merchant.get('merchant_id')}, "
            f"{merchant.get('owner_name')}): %{merchant.get('drop_pct')} düşüş, "
            f"aylık {format_try_amount(merchant.get('lost_volume_try', 0))} kayıp ciro")
    ctx.builder.add_fact("Uyuyan işletmeler (kayıp ciroya göre):\n- " + "\n- ".join(lines))

    total = sum(item.get("lost_volume_try", 0) for item in dormant)
    return (f"{len(dormant)} uyuyan isletme. Toplam kayip ciro "
            f"{format_try_amount(total)}/ay. En buyugu: {dormant[0].get('business_name')} "
            f"(%{dormant[0].get('drop_pct')} dusus).")


# ------------------------------------------------------- musteri karti

_CARD_FIELDS = ("owner_name", "business_name", "issue", "amount_mentioned_try",
                "date_mentioned", "terminal_id", "card_last4", "mood",
                "upsell_opportunity", "resolution")


# Arac argumani -> kart alani. Model zaten bu degerleri YAPISAL olarak
# uretiyor; ayrica update_customer_card cagirmasini beklemek gereksiz bir tur
# maliyeti demek (paralel arac cagirmayan modellerde tam bir LLM turu).
#
# Bu KEYWORD CIKARIMI DEGILDIR: kaynak, modelin kendi arac argumanlaridir.
_ARG_TO_CARD = {
    "amount_try": "amount_mentioned_try",
    "date": "date_mentioned",
    "terminal_id": "terminal_id",
    "card_last4": "card_last4",
    "symptom": "issue",
}


def mirror_args_to_card(ctx: ToolContext, tool_name: str, args: Dict[str, Any]) -> None:
    """Arac argumanlarindaki bilinen alanlari musteri kartina yansitir."""
    if tool_name == "update_customer_card" or not isinstance(args, dict):
        return
    card = dict(ctx.user_profile.get("card") or {})
    changed = []
    for arg_key, card_key in _ARG_TO_CARD.items():
        value = args.get(arg_key)
        if value in (None, "", []):
            continue
        if card.get(card_key) != value:
            card[card_key] = value
            changed.append(card_key)
    if changed:
        card["changed"] = changed
        ctx.user_profile["card"] = card



@tool(
    name="update_customer_card",
    description="Gorusme hafizasini gunceller: sorun, anilan tutar/tarih, ruh hali. "
                "Yeni bilgi ciktikca cagir; degismeyen alanlari GONDERME.",
    parameters={
        "type": "object",
        "properties": {
            "issue": {"type": "string", "description": "Guncel sorun, kisa Turkce"},
            "amount_mentioned_try": {"type": "number"},
            "date_mentioned": {"type": "string"},
            "terminal_id": {"type": "string"},
            "card_last4": {"type": "string"},
            "mood": {"type": "string", "enum": ["sakin", "gergin", "kizgin"]},
            "upsell_opportunity": {"type": "string"},
            "resolution": {"type": "string", "enum": ["çözüldü", "açık", "takip"],
                           "description": "Konunun durumu: çözüldü / açık / takip gerekiyor"},
        },
    },
    panel_label="hafıza güncellendi",
    requires_merchant=False,
)
def update_customer_card(ctx: ToolContext, args: Dict[str, Any]) -> str:
    """Karti ALAN BAZINDA birlestirir.

    Onceki surumde router'in dondurdugu kart sozlugu mevcut karti TAMAMEN
    eziyordu: model yalnizca {"mood": "gergin"} donerse issue, tutar ve terminal
    sessizce siliniyordu. Artik yalnizca gonderilen alanlar guncellenir.
    """
    card = dict(ctx.user_profile.get("card") or {})
    changed = []
    for field in _CARD_FIELDS:
        if field in args and args[field] not in (None, ""):
            if card.get(field) != args[field]:
                changed.append(field)
            card[field] = args[field]

    if ctx.user_profile.get("phone_number"):
        card["phone"] = ctx.user_profile["phone_number"]
    card["changed"] = changed
    ctx.user_profile["card"] = card

    return ("Hafiza guncellendi: " + ", ".join(changed)) if changed else "Hafizada degisiklik yok."


@tool(
    name="record_crm_note",
    description="Musteri hakkinda KALICI iliski bilgisi ogrenince CRM'e not "
                "duser (tasinma, rakip teklifi, buyume, memnuniyetsizlik nedeni, "
                "ozel talep/tercih). Rutin destek konusunu degil, kalici bilgiyi kaydet.",
    parameters={
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["taşınma", "rakip", "büyüme", "memnuniyetsizlik",
                         "talep", "tercih", "diğer"],
                "description": "Bilginin turu",
            },
            "note": {"type": "string", "description": "Ogrenilen bilgi, kisa Turkce"},
        },
        "required": ["category", "note"],
    },
    kind=SIDE_EFFECT,
    panel_label="CRM notu eklendi",
    once_per_turn=True,
    requires_merchant=True,
)
def record_crm_note(ctx: ToolContext, args: Dict[str, Any]) -> str:
    """Agent'in bilincli cikardigi kalici CRM icgorusunu isletmeye kaydeder."""
    if not ctx.user_profile.get("identity_verified"):
        return "Kimlik dogrulanmadi; CRM notu kaydedilmedi."
    if not ctx.merchant_id:
        return "HATA: isletme belirsiz, CRM notu kaydedilemedi."
    category = str(args.get("category") or "diğer").strip()
    note = str(args.get("note") or "").strip()
    if not note:
        return "HATA: not bos, kaydedilmedi."
    ctx.repo.add_insight(
        ctx.merchant_id, category, note[:300],
        session_id=ctx.user_profile.get("session_id", ""), channel=ctx.channel)
    return f"CRM notu kaydedildi ({category}): {note[:60]}"


@tool(
    name="set_contact_preference",
    description="Musteri 'bana X'ten ulasin/yazin' gibi bir ILETISIM TERCIHI "
                "belirtince tercih ettigi kanali kalici gunceller (telefon, "
                "whatsapp, email, sms). Yalnizca acik bir tercih belirtildiginde cagir.",
    parameters={
        "type": "object",
        "properties": {
            "channel": {"type": "string",
                        "enum": ["telefon", "whatsapp", "email", "sms"]},
        },
        "required": ["channel"],
    },
    kind=SIDE_EFFECT,
    panel_label="iletişim tercihi güncellendi",
    once_per_turn=True,
    requires_merchant=True,
)
def set_contact_preference(ctx: ToolContext, args: Dict[str, Any]) -> str:
    """Musteri iletisim tercihini merchant kaydina yazar + CRM notu birakir."""
    if not ctx.user_profile.get("identity_verified"):
        return "Kimlik dogrulanmadi; tercih guncellenmedi."
    channel = str(args.get("channel") or "").strip()
    if channel not in ("telefon", "whatsapp", "email", "sms"):
        return "HATA: gecersiz kanal."
    if not ctx.merchant_id:
        return "HATA: isletme belirsiz."
    ctx.repo.update_preferred_channel(ctx.merchant_id, channel)
    ctx.repo.add_insight(
        ctx.merchant_id, "tercih", f"İletişim tercihi: {channel}",
        session_id=ctx.user_profile.get("session_id", ""), channel=ctx.channel)
    return f"İletişim tercihi güncellendi: {channel}"


# ------------------------------------------------------------- yardimcilar

def _record_lead(ctx: ToolContext, event_type: str, payload: Dict[str, Any]) -> None:
    try:
        ctx.store.record_lead_event(ctx.user_id, event_type, payload)
    except Exception as error:                          # pragma: no cover
        print(f"Lead event warning: {error}")


def _mark_offer_made(ctx: ToolContext) -> None:
    """Teklif sayacini isaretler.

    Onceki surumde explain_fees bir FIRSAT uretip pending_offer yaziyor ama
    offer_made'i set ETMIYORDU; sonraki turda recommend_offer kontrolden gecip
    ayni cagrida IKINCI teklifi sunabiliyordu ("cagri basina 1 teklif" ihlali).
    """
    ctx.user_profile["offer_made"] = True
