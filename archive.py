"""Local archive. No cloud storage; chronological reads exclude future hands."""
import hashlib
import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from engine import number

UNITS = ('chips', 'USD', 'EUR', 'GBP', 'CHF')
DEMO_BUDGET = {'bankroll': 1000, 'stop_loss': 200, 'max_hand_loss': 100,
               'unit': 'chips', 'start_hand_id': '0', 'demo': True}


def validate_budget(value):
    if not isinstance(value, dict):
        raise ValueError('Configurazione budget non valida.')
    result = {key: round(number(value.get(key), key), 2) for key in ('bankroll', 'stop_loss', 'max_hand_loss')}
    if min(result.values()) <= 0:
        raise ValueError('Bankroll, stop loss e limite per mano devono essere maggiori di zero.')
    if result['max_hand_loss'] > result['stop_loss'] or result['stop_loss'] > result['bankroll']:
        raise ValueError('Il limite per mano deve essere ≤ stop loss ≤ bankroll.')
    if value.get('unit') not in UNITS:
        raise ValueError('Unità budget non valida.')
    start = str(value.get('start_hand_id', '0'))
    if not start.isdigit() or len(start) > 30:
        raise ValueError('Identificativo della prima mano non valido.')
    return {**result, 'unit': value['unit'], 'start_hand_id': start, 'demo': False}


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
        return json.loads(row[0]) if row else None

    def save_budget(self, data):
        budget = validate_budget(data)
        with self.connect() as db:
            db.execute("INSERT INTO settings VALUES ('budget',?) ON CONFLICT(key) DO UPDATE SET body=excluded.body", (json.dumps(budget),))
        return budget

    def before(self, hand):
        return [h for h in self.hands() if int(h['id']) < int(hand['id']) and h['unit'] == hand['unit']]
