#!/usr/bin/env bash
#
# Moka Sesli Asistan — tek giris noktasi.
#
# Sistemde iki Python var: Homebrew (bagimliliksiz) ve conda (bagimliliklarin
# yuklu oldugu). Duz "python3" yanlisi cozup "ModuleNotFoundError" verebiliyor.
# Bu betik dogru Python'u BULUR ve komutu onunla calistirir.
#
# Kullanim:
#   ./run.sh                 Sunucuyu baslat (panel 5050 + kopru 5051 + WhatsApp bot)
#   ./run.sh seed            Is verisi veritabanini kur (yoksa)
#   ./run.sh seed --force    Yeniden kur
#   ./run.sh reset           Demo verisini sifirla (konusma/gorev/lead)
#   ./run.sh reset --seed    Sifirla + panele ornek kayit koy
#   ./run.sh demo            Prova senaryolarini canli kosur (--list / --all / --only N)
#   ./run.sh mail ADRES      E-posta kurulumunu test et
#   ./run.sh test            Testleri kosur
#   ./run.sh python ...      Dogru Python'la serbest komut

set -euo pipefail
cd "$(dirname "$0")"

# --- Dogru Python'u bul: flask + dotenv import edilebilen ilk aday ----------
find_python() {
  local candidates=()
  [ -n "${CONDA_PREFIX:-}" ] && candidates+=("$CONDA_PREFIX/bin/python3")
  candidates+=("$HOME/miniconda3/bin/python3" "$HOME/anaconda3/bin/python3" "python3" "python")
  for py in "${candidates[@]}"; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c "import flask, dotenv" >/dev/null 2>&1; then
      echo "$py"; return 0
    fi
  done
  echo "HATA: flask + python-dotenv yuklu bir Python bulunamadi." >&2
  echo "  Kurulum:  pip install -r requirements.txt" >&2
  return 1
}

PY="$(find_python)"

case "${1:-server}" in
  server|"")
    echo "Python: $PY"
    exec "$PY" server.py
    ;;
  seed)
    shift
    exec "$PY" scripts/seed_demo_data.py "$@"
    ;;
  reset)
    shift
    exec "$PY" scripts/reset_demo.py "$@"
    ;;
  demo)
    shift
    exec "$PY" scripts/demo_senaryolar.py "$@"
    ;;
  mail)
    shift
    if [ $# -eq 0 ]; then echo "Kullanim: ./run.sh mail kendi@adresin.com" >&2; exit 2; fi
    exec "$PY" -m core.mailer "$@"
    ;;
  test)
    shift
    exec "$PY" -m pytest tests/ "$@"
    ;;
  python|py)
    shift
    exec "$PY" "$@"
    ;;
  *)
    echo "Bilinmeyen komut: $1" >&2
    echo "Gecerli: server | seed | reset | mail | test | python" >&2
    exit 2
    ;;
esac
