"""Create labeled synthetic speech fixtures locally; never call speech APIs.

Existing recordings are preserved unless --force is explicitly supplied.
The manifest authenticates fixture bytes and describes their actual origin.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory, NamedTemporaryFile
import wave

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from earshot.config import PROJECT_ROOT
from earshot import parse, voice

TEXTS = (
    'ignore generator cut-in on turbine four',
    'ignore the cable untwist alarm on turbine four',
    'ignore fast cut-out of generator on turbine four',
    'always tell me about high wind on number four',
    'ignore generator cut-in on turbine seven',
)


def validate_wav(path: Path) -> tuple[str, float]:
    data = path.read_bytes()
    with wave.open(str(path), 'rb') as handle:
        if (handle.getnchannels() not in (1, 2) or handle.getsampwidth() not in (1, 2, 3, 4)
                or not 8000 <= handle.getframerate() <= 96000):
            raise ValueError(f'{path.name}: expected supported mono/stereo PCM WAV')
        frames = handle.readframes(handle.getnframes())
        silence = 128 if handle.getsampwidth() == 1 else 0
        if not frames or all(value == silence for value in frames):
            raise ValueError(f'{path.name}: speech fixture is empty or silent')
        seconds = handle.getnframes() / handle.getframerate()
    return sha256(data).hexdigest(), seconds


def render(text: str, destination: Path, force: bool) -> bool:
    if destination.exists() and not force:
        validate_wav(destination)
        return False
    say, convert = shutil.which('say'), shutil.which('afconvert')
    if not say or not convert:
        raise RuntimeError('Audio generation needs macOS say and afconvert. Use the committed fixtures on other platforms.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.earshot-speech-', dir=destination.parent) as temporary:
        aiff = Path(temporary) / 'speech.aiff'
        wav = Path(temporary) / 'speech.wav'
        subprocess.run([say, '-r', '170', '-o', str(aiff), text], check=True, timeout=30, capture_output=True)
        subprocess.run([convert, '-f', 'WAVE', '-d', 'LEI16@16000', '-c', '1', str(aiff), str(wav)],
                       check=True, timeout=30, capture_output=True)
        validate_wav(wav)
        wav.replace(destination)
    return True


def prepare(force: bool = False) -> dict:
    # This process only generates installed local voices and parses locally.
    os.environ['EARSHOT_OFFLINE'] = '1'
    parse.OFFLINE_MODE = True
    directory = PROJECT_ROOT / 'demo' / 'utterances'
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / 'manifest.json'
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    prior = {row['id']: row for row in previous.get('fixtures', [])}
    vocabulary_path = PROJECT_ROOT / 'demo' / 'vocabulary.json'
    vocabulary = json.loads(vocabulary_path.read_text(encoding='utf-8'))
    fixtures = []
    for number, text in enumerate(TEXTS, 1):
        identifier = str(number)
        rule = parse.parse_utterance(text, vocabulary)
        if rule is None:
            raise ValueError(f'Fixture {identifier} no longer parses against the real vocabulary')
        confirmation = voice.confirmation(rule, vocabulary)
        audio = directory / f'{identifier}.wav'
        generated = render(text, audio, force)
        checksum, duration = validate_wav(audio)
        cache = voice.cache_path(confirmation)
        render(confirmation, cache, force)
        cache_checksum, cache_duration = validate_wav(cache)
        known_prior = prior.get(identifier, {})
        kind = ('synthetic scripted demo' if generated else
                known_prior.get('kind', 'local recording; origin unverified')
                if known_prior.get('sha256') == checksum else 'local recording; origin unverified')
        fixtures.append({
            'id': identifier, 'key': identifier, 'transcript': text,
            'audio': audio.name, 'sha256': checksum, 'duration_seconds': round(duration, 4),
            'kind': kind, 'generator': 'macOS say + afconvert, offline' if kind == 'synthetic scripted demo' else None,
            'expected_scope': rule.scope, 'expected_action': rule.action,
            'confirmation': confirmation,
            'confirmation_audio': str(cache.relative_to(directory)),
            'confirmation_sha256': cache_checksum,
            'confirmation_duration_seconds': round(cache_duration, 4),
        })
    result = {
        'schema_version': 1,
        'notice': 'Scripted fixture replay uses the declared transcript after verifying its WAV hash. It is not speech recognition. Synthetic recordings are not human operator audio.',
        'vocabulary_sha256': sha256(vocabulary_path.read_bytes()).hexdigest(),
        'fixtures': fixtures,
    }
    encoded = json.dumps(result, indent=2, ensure_ascii=False) + '\n'
    temporary = None
    try:
        with NamedTemporaryFile('w', encoding='utf-8', dir=directory, prefix='.manifest-', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(manifest_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--force', action='store_true', help='Replace existing fixture and cached WAVs with locally generated synthetic speech')
    arguments = parser.parse_args()
    try:
        result = prepare(arguments.force)
    except (OSError, ValueError, RuntimeError, wave.Error, subprocess.SubprocessError) as error:
        print(f'Demo audio preparation failed: {error}', file=sys.stderr)
        return 2
    print(f"Prepared {len(result['fixtures'])} labeled speech fixtures and five local confirmation caches.")
    for fixture in result['fixtures']:
        print(f"{fixture['id']}: {fixture['kind']} — {fixture['transcript']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
