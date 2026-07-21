"""Veriden estetik PDF raporu (LaTeX + pdflatex).

Tarayici "yazdir" ciktisi yerine gercek, markali bir PDF: Musteri-360 raporu ve
portfoy raporu LaTeX'te uretilir, pdflatex ile derlenir. Turkce karakter (T1
fontenc), sik tablolar (booktabs + colortbl), KPI kartlari (tcolorbox) ve gercek
grafikler (pgfplots) kullanilir.

pdflatex bulunamazsa PdfUnavailable firlatir; cagiran taraf zarifce dusmeli.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

# TeX Live macOS'ta genelde PATH disinda kurulu; bilinen konumlar da denenir.
_PDFLATEX_CANDIDATES = (
    "/Library/TeX/texbin/pdflatex",
    "/usr/local/texlive/2024/bin/universal-darwin/pdflatex",
    "/opt/homebrew/bin/pdflatex",
    "/usr/bin/pdflatex",
)


class PdfUnavailable(RuntimeError):
    """pdflatex sistemde yok."""


class PdfCompileError(RuntimeError):
    """LaTeX kaynagi derlenemedi (log ekli)."""


def find_pdflatex() -> Optional[str]:
    found = shutil.which("pdflatex")
    if found:
        return found
    for candidate in _PDFLATEX_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def is_available() -> bool:
    return find_pdflatex() is not None


# --------------------------------------------------------------- yardimcilar

_LATEX_MAP = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
    "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


def esc(value: Any) -> str:
    """LaTeX ozel karakterlerini kacir. Her karakter tek geciste maplenir."""
    if value is None:
        return ""
    return "".join(_LATEX_MAP.get(ch, ch) for ch in str(value))


def _money(value: Any) -> str:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return "-"
    return f"{number:,} TL".replace(",", ".")


def _short_date(value: Any) -> str:
    return esc((str(value) or "")[:10]) if value else "-"


def _bar_chart(pairs: Sequence[Tuple[str, float]], *, color: str = "moka",
               ylabel: str = "", height: str = "4.6cm", scale: float = 1.0,
               near_coords: bool = True) -> str:
    """pgfplots dikey cubuk grafik. pairs: [(etiket, deger), ...]."""
    if not pairs:
        return r"\textit{\small Veri yok.}"
    coords = " ".join(f"({esc(label)},{value * scale:.1f})" for label, value in pairs)
    symbolic = ",".join(esc(label) for label, _ in pairs)
    nnc = ("nodes near coords, every node near coord/.append style="
           r"{font=\tiny\bfseries,color=ink,/pgf/number format/fixed,"
           r"/pgf/number format/precision=0}, ") if near_coords else ""
    return (
        r"\begin{tikzpicture}"
        r"\begin{axis}[ybar, bar width=16pt, width=\linewidth, height=" + height + ", "
        r"ymajorgrids, grid style={gray!18}, axis line style={gray!40}, "
        r"axis lines=left, ymin=0, "
        r"symbolic x coords={" + symbolic + "}, xtick=data, "
        r"tick label style={font=\small\color{ink}}, ylabel={" + esc(ylabel) + "}, "
        r"ylabel style={font=\small\color{gray}}, " + nnc +
        r"enlarge x limits=0.12, clip=false]"
        r"\addplot[fill=" + color + r"!85, draw=" + color + r"] coordinates {" + coords + "};"
        r"\end{axis}\end{tikzpicture}"
    )


def _dual_bar_chart(labels: Sequence[str], series_a: Sequence[float],
                    series_b: Sequence[float], *, name_a: str, name_b: str,
                    scale: float = 1.0) -> str:
    """Iki seriyi yan yana gosteren gruplu cubuk (ciro + komisyon)."""
    if not labels:
        return r"\textit{\small Veri yok.}"
    symbolic = ",".join(esc(l) for l in labels)
    coords_a = " ".join(f"({esc(l)},{v * scale:.1f})" for l, v in zip(labels, series_a))
    coords_b = " ".join(f"({esc(l)},{v * scale:.1f})" for l, v in zip(labels, series_b))
    return (
        r"\begin{tikzpicture}"
        r"\begin{axis}[ybar, bar width=9pt, width=\linewidth, height=5cm, "
        r"ymajorgrids, grid style={gray!18}, axis line style={gray!40}, axis lines=left, ymin=0, "
        r"symbolic x coords={" + symbolic + "}, xtick=data, "
        r"tick label style={font=\small\color{ink}}, "
        r"legend style={font=\footnotesize, draw=gray!40, at={(0.5,1.08)}, anchor=south, legend columns=2}, "
        r"enlarge x limits=0.12]"
        r"\addplot[fill=moka!85, draw=moka] coordinates {" + coords_a + "};"
        r"\addplot[fill=ink!70, draw=ink] coordinates {" + coords_b + "};"
        r"\legend{" + esc(name_a) + "," + esc(name_b) + "}"
        r"\end{axis}\end{tikzpicture}"
    )


def _risk_color(tier: str) -> str:
    return {"kritik": "riskhi", "yüksek": "riskhi", "orta": "riskmid",
            "düşük": "risklo"}.get(tier, "risklo")


def _kpi_card(title: str, value: str, sub: str = "", color: str = "moka") -> str:
    sub_line = (r"\\[2pt]{\footnotesize\color{gray}" + esc(sub) + "}") if sub else ""
    return (
        r"\begin{tcolorbox}[nobeforeafter, colback=" + color + r"!7, colframe=" + color +
        r"!70, arc=2.4mm, boxrule=0.7pt, left=7pt, right=7pt, top=6pt, bottom=6pt, "
        r"width=\linewidth]"
        r"{\scriptsize\color{gray}\MakeUppercase{" + esc(title) + r"}}\\[3pt]"
        r"{\large\bfseries\color{ink}\mbox{" + esc(value) + "}}" + sub_line +
        r"\end{tcolorbox}"
    )


def _kpi_row(cards: Sequence[str]) -> str:
    """Esit genislikte KPI kartlari yan yana."""
    n = len(cards)
    width = f"{0.99 / n:.3f}\\linewidth"
    cells = []
    for card in cards:
        cells.append(r"\begin{minipage}[t]{" + width + "}" + card + r"\end{minipage}")
    return "\\hfill".join(cells)


# --------------------------------------------------------------- preamble

_PREAMBLE = r"""\documentclass[10pt]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage[a4paper,margin=1.5cm,top=2.4cm,bottom=2cm]{geometry}
\usepackage[table]{xcolor}
\usepackage{booktabs}
\usepackage{array}
\usepackage[most]{tcolorbox}
\usepackage{fancyhdr}
\usepackage{pgfplots}
\usepackage{tikz}
\usepackage{amssymb}
\usepackage{graphicx}
\usepackage{enumitem}
\pgfplotsset{compat=1.18}
\usepackage[hidelinks]{hyperref}

% pdflatex + T1 bazi Unicode sembolleri tanimaz; metinde kullanilanlari tanit.
\DeclareUnicodeCharacter{25B2}{\ensuremath{\blacktriangle}}
\DeclareUnicodeCharacter{25BC}{\ensuremath{\blacktriangledown}}
\DeclareUnicodeCharacter{00B7}{\textperiodcentered}
\DeclareUnicodeCharacter{2022}{\textbullet}
\DeclareUnicodeCharacter{2013}{\textendash}
\DeclareUnicodeCharacter{2014}{\textemdash}
\DeclareUnicodeCharacter{2192}{\ensuremath{\rightarrow}}

\definecolor{moka}{RGB}{0,166,80}
\definecolor{ink}{RGB}{23,48,66}
\definecolor{riskhi}{RGB}{176,58,46}
\definecolor{riskmid}{RGB}{176,112,20}
\definecolor{risklo}{RGB}{40,110,150}
\definecolor{softline}{RGB}{225,231,224}

\setlength{\parindent}{0pt}
\setlength{\arrayrulewidth}{0.4pt}
\arrayrulecolor{softline}
\renewcommand{\arraystretch}{1.25}

\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0.4pt}
\fancyfoot[L]{\footnotesize\color{gray}__PROJECT__ \textbullet\ __FOOTER__}
\fancyfoot[R]{\footnotesize\color{gray}Sayfa \thepage}
\fancyfoot[C]{\footnotesize\color{gray}__GENERATED__}

% Markali ust bant
\newcommand{\reportheader}[2]{%
  \begin{tcolorbox}[colback=moka, colframe=moka, arc=2mm, boxrule=0pt, left=12pt, right=12pt, top=9pt, bottom=9pt]
    {\color{white}\Large\bfseries #1}\hfill{\color{white!85}\small #2}
  \end{tcolorbox}\vspace{4pt}
}

% Bolum basligi
\newcommand{\sectionband}[1]{\vspace{9pt}{\color{ink}\large\bfseries #1}\\[-4pt]{\color{moka}\rule{\linewidth}{1.4pt}}\vspace{4pt}\par}

\begin{document}
"""


_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGO_PATH = os.path.join(_BASE_DIR, "assets", "moka-logo.pdf")


def _brand_bar(footer_label: str, generated_on: str) -> str:
    """Rapor basi: Moka logosu (sol) + rapor tipi/tarih (sag) + ince ayrac.

    Logo yesil banttan ONCE, beyaz zeminde durur — marka renkleri (lacivert +
    mint) yesil ustunde kaybolmasin. Logo yoksa (rsvg uretmemis) atlanir."""
    if not os.path.exists(_LOGO_PATH):
        return ""
    return (r"\includegraphics[height=7.5mm]{" + _LOGO_PATH + r"}\hfill"
            r"{\footnotesize\color{gray}" + esc(footer_label) + r" \textbullet\ " +
            esc(generated_on) + r"}\\[2pt]"
            r"{\color{softline}\rule{\linewidth}{0.6pt}}\vspace{5pt}" + "\n")


def _document(project_name: str, generated_on: str, body: str,
              footer_label: str = "Müşteri Raporu") -> str:
    preamble = (_PREAMBLE
                .replace("__PROJECT__", esc(project_name))
                .replace("__FOOTER__", esc(footer_label))
                .replace("__GENERATED__", esc(generated_on)))
    return preamble + _brand_bar(footer_label, generated_on) + body + "\n\\end{document}\n"


# ----------------------------------------------------------- musteri raporu

def render_customer_report(data: Dict[str, Any], *, project_name: str,
                           generated_on: str) -> str:
    m = data.get("merchant", {})
    plan = data.get("plan") or {}
    risk = data.get("risk") or {}
    trend = data.get("volume_trend") or {}
    monthly = data.get("monthly") or {}
    upgrade = data.get("upgrade")

    parts: List[str] = []
    parts.append(r"\reportheader{" + esc(m.get("business_name", "")) + "}{" +
                 esc(f"{m.get('sector', '')} — {m.get('city', '')}/{m.get('district', '')} — {m.get('merchant_id', '')}") + "}")

    # Risk seridi
    rc = _risk_color(risk.get("risk_tier", ""))
    status_tag = ""
    if (m.get("status") or "active") != "active":
        status_tag = (r"\hfill\colorbox{riskmid!85}{\color{white}\small\bfseries\ " +
                      esc(m.get("status")) + r"\ }")
    parts.append(
        r"\colorbox{" + rc + r"!90}{\color{white}\small\bfseries\ " +
        esc(f"{risk.get('risk_tier', '')} risk · {risk.get('risk_score', '')}/100") +
        r"\ }\quad\small\color{gray}" + esc(f"Segment: {risk.get('segment', '')}") +
        r"\quad Temsilci: " + esc(m.get("account_manager") or "atanmadı") + status_tag + r"\par\vspace{8pt}")

    # KPI kartlari
    change = trend.get("change_pct", 0)
    change_sub = (f"▲ %{change}" if change > 0 else (f"▼ %{abs(change)}" if change < 0 else "%0"))
    cards = [
        _kpi_card("Bu ay ciro", _money(trend.get("last_month")), change_sub, "moka"),
        _kpi_card("Bu ay komisyon", _money(monthly.get("commission_try")),
                  f"%{monthly.get('rate_pct', 0)} · {plan.get('name', '')}", "ink"),
        _kpi_card("Risk skoru", f"{risk.get('risk_score', '')}/100",
                  risk.get("risk_tier", ""), rc),
    ]
    if upgrade:
        cards.append(_kpi_card("Plan fırsatı", _money(upgrade.get("monthly_saving_try")),
                               f"{upgrade['plan'].get('name', '')} ile/ay", "moka"))
    else:
        last_contact = data.get("contacts", [{}])
        lc = last_contact[0].get("contacted_at") if last_contact else ""
        cards.append(_kpi_card("Son temas", _short_date(lc), m.get("account_manager") or "—", "risklo"))
    parts.append(_kpi_row(cards) + r"\vspace{10pt}")

    # Ciro trendi grafik + profil (yan yana)
    series = m.get("monthly_volume_try") or []
    chart_pairs = [((entry.get("month") or "")[5:], entry.get("volume", 0)) for entry in series]
    parts.append(r"\begin{minipage}[t]{0.60\linewidth}")
    parts.append(r"\sectionband{Aylık ciro trendi (bin TL)}")
    parts.append(_bar_chart(chart_pairs, ylabel="bin TL", scale=0.001))
    if risk.get("reasons"):
        parts.append(r"\vspace{2pt}{\footnotesize\color{gray}\textbf{Risk notu:} " +
                     esc(" · ".join(risk["reasons"])) + "}")
    parts.append(r"\end{minipage}\hfill\begin{minipage}[t]{0.37\linewidth}")
    parts.append(r"\sectionband{Profil}")
    profile_rows = [
        ("Sahip", m.get("owner_name")),
        ("Plan", f"{plan.get('name', '')} (%{plan.get('rate_pct', '')})"),
        ("Tier", m.get("tier")),
        ("İletişim", m.get("preferred_channel")),
        ("Telefon", m.get("phone")),
        ("E-posta", m.get("email")),
        ("IBAN", m.get("iban_masked")),
        ("Katılım", m.get("joined")),
        ("Ürünler", ", ".join(m.get("products") or [])),
    ]
    body_rows = " ".join(
        r"{\color{gray}\footnotesize " + esc(k) + r"} & {\footnotesize " + esc(v) + r"}\\"
        for k, v in profile_rows)
    parts.append(r"{\renewcommand{\arraystretch}{1.05}"
                 r"\begin{tabular}{@{}>{\raggedright}p{2.0cm}>{\raggedright\arraybackslash}p{4.3cm}@{}}" +
                 body_rows + r"\end{tabular}}")
    if m.get("notes"):
        parts.append(r"\vspace{4pt}{\footnotesize\itshape\color{gray}" + esc(m.get("notes")) + "}")
    parts.append(r"\end{minipage}\par")

    # Hakedisler + islemler (yan yana tablolar)
    parts.append(r"\begin{minipage}[t]{0.48\linewidth}")
    parts.append(r"\sectionband{Son hakedişler}")
    parts.append(_settlements_table(data.get("settlements", [])))
    parts.append(r"\end{minipage}\hfill\begin{minipage}[t]{0.48\linewidth}")
    parts.append(r"\sectionband{Son işlemler}")
    parts.append(_transactions_table(data.get("transactions", [])[:6]))
    parts.append(r"\end{minipage}\par")

    # Konusma -> not kapali dongusu: AI icgoruleri (kapali dongu, raporun 'not'
    # ayagi) ile temas gecmisi YAN YANA — dikey yer kazanip tek sayfada tutar.
    insights = data.get("insights", [])
    contacts = [c for c in data.get("contacts", []) if c.get("source") != "insight"]
    if insights:
        parts.append(r"\noindent\begin{minipage}[t]{0.49\linewidth}")
        parts.append(r"\sectionband{AI İçgörüleri \small(konuşmalardan)}")
        parts.append(_insights_table(insights))
        parts.append(r"\end{minipage}\hfill\begin{minipage}[t]{0.49\linewidth}")
        parts.append(r"\sectionband{Temas geçmişi}")
        parts.append(_contacts_table(contacts))
        parts.append(r"\end{minipage}\par")
    else:
        parts.append(r"\sectionband{Temas geçmişi}")
        parts.append(_contacts_table(contacts))

    return _document(project_name, generated_on, "\n".join(parts),
                     footer_label="Müşteri Raporu")


def _insights_table(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return r"\textit{\small İçgörü yok.}"
    body = []
    for i in rows[:8]:
        body.append(
            r"{\footnotesize\bfseries\color{moka}" + esc(i.get("subject")) + r"} "
            r"{\scriptsize\color{gray}" + _short_date(i.get("contacted_at")) + r"} & "
            r"{\footnotesize " + esc((i.get("note") or "")[:80]) + r"}\\")
    return (r"\rowcolors{2}{moka!7}{white}"
            r"\begin{tabular}{@{}>{\raggedright}p{2.6cm}>{\raggedright\arraybackslash}p{5.4cm}@{}}\toprule "
            r"{\footnotesize\bfseries Kategori} & {\footnotesize\bfseries Konuşmada öğrenilen}\\\midrule "
            + (" ".join(body)) + r"\bottomrule\end{tabular}")


def _settlements_table(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return r"\textit{\small Hakediş kaydı yok.}"
    body = []
    for s in rows[:6]:
        body.append(" & ".join([
            esc(s.get("batch_id")), _short_date(s.get("batch_date")),
            _money(s.get("gross_try")), esc(s.get("status"))]) + r"\\")
    return (r"\rowcolors{2}{moka!5}{white}"
            r"\begin{tabular}{@{}llrl@{}}\toprule "
            r"{\footnotesize\bfseries Parti} & {\footnotesize\bfseries Tarih} & "
            r"{\footnotesize\bfseries Brüt} & {\footnotesize\bfseries Durum}\\\midrule "
            r"\footnotesize " + (r"\footnotesize ".join(body)) + r"\bottomrule\end{tabular}")


def _transactions_table(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return r"\textit{\small İşlem kaydı yok.}"
    body = []
    for t in rows:
        body.append(" & ".join([
            esc((t.get("timestamp") or "")[:16].replace("T", " ")),
            _money(t.get("amount_try")), esc(t.get("channel")), esc(t.get("status"))]) + r"\\")
    return (r"\rowcolors{2}{ink!4}{white}"
            r"\begin{tabular}{@{}lrll@{}}\toprule "
            r"{\footnotesize\bfseries Tarih} & {\footnotesize\bfseries Tutar} & "
            r"{\footnotesize\bfseries Kanal} & {\footnotesize\bfseries Durum}\\\midrule "
            r"\footnotesize " + (r"\footnotesize ".join(body)) + r"\bottomrule\end{tabular}")


def _contacts_table(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return r"\textit{\small Temas kaydı yok.}"
    body = []
    for c in rows[:8]:
        outcome = c.get("outcome")
        tag = (r" {\scriptsize\bfseries\color{moka}[" + esc(outcome) + "]}") if outcome else ""
        body.append(
            r"{\footnotesize " + esc((c.get("subject") or "")[:24]) + r"}" + tag + r" "
            r"{\scriptsize\color{gray}" + _short_date(c.get("contacted_at")) +
            r" · " + esc(c.get("channel")) + r"} & "
            r"{\footnotesize " + esc((c.get("note") or "")[:60]) + r"}\\")
    return (r"\rowcolors{2}{moka!5}{white}"
            r"\begin{tabular}{@{}>{\raggedright}p{2.9cm}>{\raggedright\arraybackslash}p{5.1cm}@{}}\toprule "
            r"{\footnotesize\bfseries Konu} & {\footnotesize\bfseries Not}\\\midrule "
            + (" ".join(body)) + r"\bottomrule\end{tabular}")


# ----------------------------------------------------------- portfoy raporu

def render_portfolio_report(summary: Dict[str, Any], *, project_name: str,
                            generated_on: str) -> str:
    parts: List[str] = []
    parts.append(r"\reportheader{Portföy Raporu}{" +
                 esc(f"{summary.get('merchant_count', 0)} işletme — tüm portföy") + "}")

    seg = summary.get("count_by_segment", {})
    risk = summary.get("count_by_risk_tier", {})
    cards = [
        _kpi_card("İşletme", str(summary.get("merchant_count", 0)), "portföy", "ink"),
        _kpi_card("Bu ay ciro", _money(summary.get("total_last_month_try")), "portföy geneli", "moka"),
        _kpi_card("Bu ay komisyon", _money(summary.get("total_commission_try")), "tahmini gelir", "moka"),
        _kpi_card("Riskli", str(risk.get("kritik", 0) + risk.get("yüksek", 0)),
                  "kritik + yüksek", "riskhi"),
        _kpi_card("Uyuyan", str(seg.get("uyuyan", 0)), "acil temas", "riskmid"),
    ]
    parts.append(_kpi_row(cards) + r"\par\vspace{12pt}")

    # Ciro + komisyon gruplu grafik (tam genislik; sonrasinda \par SART, yoksa
    # ardindan gelen minipage'ler tikzpicture ile ayni satira dizilir ve tasar).
    monthly = summary.get("monthly_totals", [])
    labels = [(m.get("month") or "")[5:] for m in monthly]
    parts.append(r"\sectionband{Aylık ciro ve komisyon (bin TL)}")
    parts.append(_dual_bar_chart(
        labels, [m.get("volume_try", 0) for m in monthly],
        [m.get("commission_try", 0) for m in monthly],
        name_a="Ciro", name_b="Komisyon", scale=0.001) + r"\par\vspace{12pt}")

    # Islem hacmi grafigi (yarim) + dagilim tablosu (yarim), yan yana.
    txn = summary.get("txn_volume_by_month", [])
    parts.append(r"\noindent\begin{minipage}[t]{0.55\linewidth}")
    parts.append(r"\sectionband{Aylık işlem hacmi (adet)}")
    parts.append(_bar_chart([((t.get("month") or "")[5:], t.get("count", 0)) for t in txn],
                            color="risklo", ylabel="adet"))
    parts.append(r"\end{minipage}\hfill\begin{minipage}[t]{0.41\linewidth}")
    parts.append(r"\sectionband{Segment \& risk dağılımı}")
    parts.append(_distribution_table(seg, risk, summary.get("plan_distribution", {})))
    parts.append(r"\end{minipage}\par\vspace{10pt}")

    # Top listeler (iki tablo yan yana)
    parts.append(r"\noindent\begin{minipage}[t]{0.48\linewidth}")
    parts.append(r"\sectionband{En hızlı büyüyen 5}")
    parts.append(_top_table(summary.get("top_growing", []), growing=True))
    parts.append(r"\end{minipage}\hfill\begin{minipage}[t]{0.48\linewidth}")
    parts.append(r"\sectionband{En riskli 5 (uyuyan/daralan)}")
    parts.append(_top_table(summary.get("top_dormant", []), growing=False))
    parts.append(r"\end{minipage}\par")

    return _document(project_name, generated_on, "\n".join(parts),
                     footer_label="Portföy Raporu")


def _distribution_table(seg: Dict[str, int], risk: Dict[str, int],
                        plan: Dict[str, int]) -> str:
    def block(title: str, mapping: Dict[str, int]) -> str:
        rows = " ".join(
            r"{\footnotesize " + esc(k) + r"} & {\footnotesize\bfseries " + str(v) + r"}\\"
            for k, v in mapping.items())
        return (r"{\footnotesize\bfseries\color{moka}" + esc(title) + r"}\\[2pt]"
                r"\begin{tabular}{@{}>{\raggedright}p{4.4cm}r@{}}" + rows + r"\end{tabular}\\[6pt]")
    return block("Segment", seg) + block("Risk kademesi", risk) + block("Plan", plan)


def _top_table(rows: List[Dict[str, Any]], *, growing: bool) -> str:
    if not rows:
        return r"\textit{\small Kayıt yok.}"
    body = []
    for c in rows:
        if growing:
            metric = r"{\color{moka}\bfseries " + esc(f"▲ %{c.get('change_pct', 0)}") + "}"
        else:
            metric = r"{\color{riskhi}\bfseries " + esc(f"{c.get('risk_tier', '')}·{c.get('risk_score', '')}") + "}"
        body.append(r"{\footnotesize " + esc(c.get("business_name", "")) + "} & " +
                    r"{\footnotesize " + _money(c.get("last_month_try")) + "} & " + metric + r"\\")
    return (r"\rowcolors{2}{moka!5}{white}\footnotesize"
            r"\begin{tabular}{@{}>{\raggedright\arraybackslash}p{3.9cm}r>{\raggedleft\arraybackslash}p{1.7cm}@{}}\toprule "
            r"{\footnotesize\bfseries İşletme} & {\footnotesize\bfseries Bu ay} & "
            r"{\footnotesize\bfseries " + ("Değişim" if growing else "Risk") + r"}\\\midrule " +
            (" ".join(body)) + r"\bottomrule\end{tabular}")


# ----------------------------------------------------------------- derleme

def compile_pdf(tex_source: str, *, timeout: int = 60) -> bytes:
    """LaTeX kaynagini pdflatex ile derler, PDF baytlarini dondurur."""
    pdflatex = find_pdflatex()
    if not pdflatex:
        raise PdfUnavailable("pdflatex bulunamadı; TeX Live kurulu değil.")

    env = dict(os.environ)
    bindir = os.path.dirname(pdflatex)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")

    with tempfile.TemporaryDirectory() as tmp:
        tex_path = os.path.join(tmp, "report.tex")
        with open(tex_path, "w", encoding="utf-8") as handle:
            handle.write(tex_source)
        try:
            subprocess.run(
                [pdflatex, "-interaction=nonstopmode", "-halt-on-error", "report.tex"],
                cwd=tmp, env=env, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            raise PdfCompileError("pdflatex zaman aşımına uğradı.") from error

        pdf_path = os.path.join(tmp, "report.pdf")
        if not os.path.exists(pdf_path):
            log_path = os.path.join(tmp, "report.log")
            log = ""
            if os.path.exists(log_path):
                with open(log_path, encoding="utf-8", errors="replace") as handle:
                    log = handle.read()[-1500:]
            raise PdfCompileError(f"PDF üretilemedi.\n{log}")

        with open(pdf_path, "rb") as handle:
            return handle.read()


def customer_pdf(data: Dict[str, Any], *, project_name: str = "Moka Sesli Asistan",
                 generated_on: Optional[str] = None) -> bytes:
    generated_on = generated_on or date.today().strftime("%d.%m.%Y")
    return compile_pdf(render_customer_report(
        data, project_name=project_name, generated_on=generated_on))


def portfolio_pdf(summary: Dict[str, Any], *, project_name: str = "Moka Sesli Asistan",
                  generated_on: Optional[str] = None) -> bytes:
    generated_on = generated_on or date.today().strftime("%d.%m.%Y")
    return compile_pdf(render_portfolio_report(
        summary, project_name=project_name, generated_on=generated_on))
