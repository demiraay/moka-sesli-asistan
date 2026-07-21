"""SQLite baglanti ve migration altyapisi.

Eski yaklasim: her AdminStore() kurulumunda "CREATE TABLE IF NOT EXISTS" +
"ALTER TABLE ADD COLUMN" turu ileri-only sema kodu calisiyordu; versiyon takibi,
index yonetimi ve geri alma yoktu. Burasi bunu PRAGMA user_version tabanli
numarali migration'a cevirir.

Ayrica eski _connect() su PRAGMA'lari HIC ayarlamiyordu:
  - journal_mode=WAL   : panel (5050) ve WhatsApp koprusu (5051) AYRI process
                         olarak ayni dosyaya yaziyor; DELETE modunda yazar
                         okuyucuyu bloklar ve "database is locked" 500 uretir.
  - busy_timeout       : varsayilan 5sn, acikca ayarlanir.
  - foreign_keys=ON    : SQLite'ta varsayilan KAPALI; FK tanimlari fiilen etkisizdi.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Sequence, Tuple, Union

# (version, name, sql_statements)
Migration = Tuple[int, str, Sequence[str]]

PathLike = Union[str, Path]


def connect(db_path: PathLike) -> sqlite3.Connection:
    """Proje genelinde TEK baglanti kurma yolu.

    PRAGMA foreign_keys BAGLANTI basinadir (sema basina degil) — her yeni
    baglantida yeniden ayarlanmak zorunda.
    """
    connection = sqlite3.connect(str(db_path), timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


@contextmanager
def session(db_path: PathLike) -> Iterator[sqlite3.Connection]:
    """Basarida commit, hatada rollback, her durumda close."""
    connection = connect(db_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def migrate(db_path: PathLike, migrations: Sequence[Migration]) -> int:
    """Uygulanmamis migration'lari sirayla calistirir, son versiyonu dondurur.

    Her migration tek bir transaction icinde kosar: yarida patlarsa ROLLBACK
    edilir ve user_version ARTMAZ, yani bir sonraki calistirmada bastan denenir.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connection = connect(db_path)
    try:
        current = get_version(connection)
        for version, name, statements in sorted(migrations, key=lambda item: item[0]):
            if version <= current:
                continue
            try:
                connection.execute("BEGIN")
                for statement in statements:
                    connection.execute(statement)
                # PRAGMA parametre baglamayi desteklemez; version int literal.
                connection.execute(f"PRAGMA user_version = {int(version)}")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise RuntimeError(f"Migration basarisiz: v{version} ({name})")
            current = version
        return current
    finally:
        connection.close()


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def column_names(connection: sqlite3.Connection, table_name: str) -> List[str]:
    return [row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})")]
