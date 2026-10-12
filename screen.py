"""Game-agnostic screenshot analysis. No image persistence or input automation."""
import base64
import binascii
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading

MAX_FRAMES = 3
MAX_IMAGE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 4_000_000
SCREEN_WORK = threading.BoundedSemaphore(1)
PROMPT = Path(__file__).resolve().parent / 'prompts' / 'screen_system.txt'


def object_schema(properties):
    return {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}


TEXT = {'type': 'string'}
OPTIONAL_TEXT = {'type': ['string', 'null']}
TEXTS = {'type': 'array', 'items': TEXT}
CONFIDENCE = {'type': 'string', 'enum': ['low', 'medium', 'high']}
MOVE = object_schema({'action': TEXT, 'reason': TEXT, 'steps': TEXTS})
SCHEMA = object_schema({
    'status': {'type': 'string', 'enum': ['ready', 'needs_context', 'not_game']},
    'game': object_schema({'title': OPTIONAL_TEXT, 'genre': OPTIONAL_TEXT, 'confidence': CONFIDENCE}),
    'summary': TEXT,
    'observations': TEXTS,
    'changes': TEXTS,
    'turn': {'type': 'string', 'enum': ['player', 'opponent', 'simultaneous', 'unknown', 'not_applicable']},
    'next_move': {'anyOf': [MOVE, {'type': 'null'}]},
    'alternatives': {'type': 'array', 'items': object_schema({'action': TEXT, 'reason': TEXT})},
    'uncertainties': TEXTS,
    'question': OPTIONAL_TEXT,
    'confidence': CONFIDENCE,
})


def short_text(value, limit, label, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{label}: testo mancante o troppo lungo (massimo {limit} caratteri).')
    return value.strip()


def validate_image(url):
    if not isinstance(url, str) or len(url) > 2_666_700:
        raise ValueError('Ogni fotogramma deve pesare al massimo 2 MB.')
    match = re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)', url)
    if not match:
        raise ValueError('Usa immagini PNG, JPEG o WebP in formato data URL.')
    try:
        raw = base64.b64decode(match[2], validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('Immagine non valida.') from None
    signatures = {
        'png': raw.startswith(b'\x89PNG\r\n\x1a\n'),
        'jpeg': raw.startswith(b'\xff\xd8\xff'),
        'webp': raw.startswith(b'RIFF') and raw[8:12] == b'WEBP',
    }
    if not signatures[match[1]] or len(raw) < 16:
        raise ValueError('Il contenuto non corrisponde al formato dell’immagine.')
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError('Ogni fotogramma deve pesare al massimo 2 MB.')
    return len(raw)


def validate_request(data):
    if data.get('consent') is not True:
        raise ValueError('Autorizza l’invio dei fotogrammi selezionati a OpenAI.')
    frames = data.get('frames')
    if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAMES:
        raise ValueError('Invia da uno a tre fotogrammi in ordine cronologico.')
    total = 0
    previous = None
    clean = []
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError('Fotogramma non valido.')
        total += validate_image(frame.get('image'))
        stamp = short_text(frame.get('captured_at'), 40, 'Data del fotogramma')
        try:
            captured = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            if captured.tzinfo is None or captured.utcoffset() is None:
                raise ValueError()
            captured = captured.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise ValueError('Data del fotogramma non valida: usa ISO 8601 con fuso orario.') from None
        if previous is not None and captured <= previous:
            raise ValueError('I fotogrammi devono avere date crescenti.')
        previous = captured
        clean.append({'image': frame['image'], 'captured_at': captured.isoformat()})
    if total > MAX_TOTAL_BYTES:
        raise ValueError('I fotogrammi insieme devono pesare al massimo 4 MB.')
    context = {}
    for key, limit in (('game_hint', 120), ('goal', 1000)):
        value = data.get(key, '')
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f'{key}: usa al massimo {limit} caratteri.')
        context[key] = value.strip()
    return clean, context


def validate_schema(value, schema):
    """Validate provider output independently of its Structured Outputs promise."""
    if 'anyOf' in schema:
        for choice in schema['anyOf']:
            try:
                validate_schema(value, choice)
                return
            except ValueError:
                pass
        raise ValueError('Tipo di proposta non valido.')
    kind = schema['type']
    if isinstance(kind, list):
        if value is None and 'null' in kind:
            return
        kind = 'string'
    if kind == 'null':
        valid = value is None
    elif kind == 'string':
        valid = isinstance(value, str) and bool(value.strip()) and len(value) <= 1200
    elif kind == 'array':
        valid = isinstance(value, list) and len(value) <= 8
        if valid:
            for item in value:
                validate_schema(item, schema['items'])
    elif kind == 'object':
        valid = isinstance(value, dict) and set(value) == set(schema['properties'])
        if valid:
            for key, spec in schema['properties'].items():
                validate_schema(value[key], spec)
    else:
        valid = False
    if not valid or ('enum' in schema and value not in schema['enum']):
        raise ValueError('Formato della risposta AI non valido.')


def validate_answer(answer):
    validate_schema(answer, SCHEMA)
    if len(answer['alternatives']) > 3:
        raise ValueError('Troppe alternative nella risposta AI.')
    # Fail closed when the model's own evidence cannot support an action.
    if answer['status'] == 'ready' and (
            answer['next_move'] is None or answer['confidence'] == 'low'
            or answer['game']['confidence'] == 'low'
            or answer['turn'] in ('unknown', 'opponent')):
        answer['status'] = 'needs_context'
        answer['uncertainties'] = (answer['uncertainties'] + [
            'Mossa sospesa: riconoscimento, turno o contesto non sufficientemente chiari.'
        ])[:8]
    if answer['status'] != 'ready':
        answer['next_move'] = None
        answer['alternatives'] = []
        if answer['status'] == 'needs_context' and not answer['question']:
            answer['question'] = 'Puoi mostrare meglio lo stato, le regole e il turno del giocatore?'
    return answer


def analyze_screen(data, ai_call, model):
    frames, context = validate_request(data)
    content = [{'type': 'input_text', 'text': json.dumps({
        'task': 'Analizza la situazione dell’ULTIMO fotogramma e proponi la prossima mossa.',
        'user_context': context,
        'frame_count': len(frames),
        'timestamps_mean': 'Istante di cattura o caricamento; non prova dell’ora originale del gioco.',
    }, ensure_ascii=False)}]
    for index, frame in enumerate(frames):
        content.extend([
            {'type': 'input_text', 'text': f'Fotogramma {index + 1}/{len(frames)} · {frame["captured_at"]}'},
            {'type': 'input_image', 'image_url': frame['image'], 'detail': 'high'},
        ])
    if not SCREEN_WORK.acquire(blocking=False):
        raise ValueError('Un’analisi dello schermo è già in corso. Attendi prima di riprovare.')
    try:
        raw = ai_call(PROMPT.read_text(), content, SCHEMA)
        try:
            answer = validate_answer(json.loads(raw))
            if len(frames) == 1:
                answer['changes'] = []  # No temporal evidence exists in a single image.
        except (ValueError, TypeError, RecursionError):
            raise ValueError('Risposta visiva non valida. Nessuna mossa disponibile; riprova con un’immagine più chiara.') from None
    finally:
        SCREEN_WORK.release()
    return {**answer, 'model': model, 'frame_count': len(frames),
            'analyzed_at': datetime.now(timezone.utc).isoformat(),
            'captured_at': frames[-1]['captured_at'], 'source': 'screen_ai'}
