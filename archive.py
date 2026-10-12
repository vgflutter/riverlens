"""Local archive. No cloud storage; chronological reads exclude future hands."""
import hashlib
import json
from decimal import Decimal
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from engine import number

UNITS = ('chips', 'USD', 'EUR', 'GBP', 'CHF')


def validate_budget(value):
    if not isinstance(value, dict):
        raise ValueError('Configurazione budget non valida.')
    if set(value) - {'stop_loss', 'max_hand_loss', 'unit', 'start_hand_id'}:
        raise ValueError('Configura solo limiti personali: stack e saldo non si impostano qui.')
    result = {}
    for key in ('stop_loss', 'max_hand_loss'):
        raw = value.get(key)
        if raw is None or raw == '':
            result[key] = None
            continue
        amount = number(raw, key)
        scaled = Decimal(str(amount)) * 100
        if amount <= 0 or scaled != scaled.to_integral_value():
            raise ValueError('I limiti devono essere positivi, con al massimo due decimali, oppure vuoti.')
        result[key] = amount
    enabled = any(x is not None for x in result.values())
    unit = value.get('unit')
    if enabled and unit not in UNITS:
        raise ValueError('L’unità dei limiti deve corrispondere al tavolo.')
    start = str(value.get('start_hand_id', '0'))
    if not start.isdigit() or len(start) > 30:
        raise ValueError('Identificativo della prima mano non valido.')
    return {**result, 'unit': unit if enabled else None, 'start_hand_id': start}


class Archive:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('CREATE TABLE IF NOT EXISTS hands (id TEXT PRIMARY KEY, digest TEXT NOT NULL, body TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, body TEXT NOT NULL)')
        try:
            with db:
                yield db
        finally:
            db.close()

    def import_hands(self, hands):
        added = duplicates = 0
        with self.connect() as db:
            for hand in hands:
                text = json.dumps(hand, sort_keys=True, ensure_ascii=False)
                digest = hashlib.sha256(text.encode()).hexdigest()
                old = db.execute('SELECT digest FROM hands WHERE id=?', (hand['id'],)).fetchone()
                if old:
                    if old[0] != digest:
                        raise ValueError(f"La mano {hand['id']} è già archiviata con dati diversi; importazione annullata.")
                    duplicates += 1
                    continue
                db.execute('INSERT INTO hands VALUES (?,?,?)', (hand['id'], digest, text))
                added += 1
        return {'added': added, 'duplicates': duplicates}

    def hands(self):
        with self.connect() as db:
            rows = db.execute('SELECT body FROM hands ORDER BY length(id), id').fetchall()
        return [json.loads(row[0]) for row in rows]

    def budget(self):
        with self.connect() as db:
            row = db.execute("SELECT body FROM settings WHERE key='budget'").fetchone()
        if not row:
            return None
        value = json.loads(row[0])
        # v0.2 required a manually entered bankroll. Retain chosen loss limits only.
        value.pop('bankroll', None)
        value.pop('demo', None)
        return validate_budget(value)

    def save_budget(self, data):
        budget = validate_budget(data)
        with self.connect() as db:
            db.execute("INSERT INTO settings VALUES ('budget',?) ON CONFLICT(key) DO UPDATE SET body=excluded.body", (json.dumps(budget),))
        return budget

    def before(self, hand):
        return [h for h in self.hands() if int(h['id']) < int(hand['id']) and h['unit'] == hand['unit']]
