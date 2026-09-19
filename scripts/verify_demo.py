"""Rehearse an already-running, freshly prewarmed local demo; undo only our rules."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
import sys
from time import perf_counter

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import websockets
from earshot.config import CONFIG, PROJECT_ROOT


async def main():
    start = perf_counter()
    scenario = json.loads((PROJECT_ROOT / 'demo/scenario.json').read_text())
    beats = {row['id']: row for row in scenario['beats']}
    evidence, learned = {}, []
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000', timeout=15) as client:
        async def call(method, path, body=None):
            response = await client.request(method, path, json=body) if body is not None else await client.request(method, path)
            response.raise_for_status()
            return response.json()
        initial = await call('GET', '/stats')
        evidence['initial'] = {k:v for k,v in initial.items() if k != 'baseline'}
        assert initial['replay']['paused'], 'Start with a fresh run_demo.sh prewarm'
        assert initial['active_rules'] == 0, 'Undo your own demo rules before this rehearsal; this script never deletes existing rules'
        prior_online = initial['online']
        try:
            async with websockets.connect('ws://127.0.0.1:8000/stream') as stream:
                async def until(wanted, timeout=30):
                    async with asyncio.timeout(timeout):
                        async for raw in stream:
                            message = json.loads(raw)
                            if wanted(message):
                                return message
                    raise AssertionError('Expected real event was not received')
                def match(expected):
                    return lambda m: m.get('type') == 'event' and all(m.get('event', {}).get(k) == expected[k] for k in ('ts','turbine_id','alarm_code'))
                learned_start = perf_counter()
                first = await call('POST', '/teach', {'text':beats['teach']['utterance']})
                learned.append(first['rule_id'])
                evidence['teach_ms'] = round((perf_counter()-learned_start)*1000, 3)
                evidence['teach'] = first
                assert first['model_version'] == initial['model_version'] + 1
                assert first['newly_suppressed_count'] == 4
                resumed_at = perf_counter()
                await call('POST', '/replay', {'action':'resume'})
                recurrence = await until(match(beats['proof']['suppressed_event']))
                evidence['recurrence_seconds_after_resume'] = round(perf_counter() - resumed_at, 3)
                assert 9 <= evidence['recurrence_seconds_after_resume'] < 15, 'Prewarmed replay clock must preserve 15x pacing'
                assert not recurrence['verdict']['show']
                evidence['recurrence'] = recurrence
                untouched = await until(match(beats['proof']['untaught_stopping_event']))
                evidence['stopping_seconds_after_resume'] = round(perf_counter() - resumed_at, 3)
                assert untouched['verdict']['show'] and untouched['event']['stopping'] == 1
                evidence['untouched_stopping'] = untouched
                await call('POST', '/replay', {'action':'pause'})
                evidence['killswitch'] = await call('POST', '/killswitch', {'on':True})
                second = await call('POST', '/teach', {'text':beats['unplug']['utterance']})
                learned.append(second['rule_id'])
                assert second['model_version'] == first['model_version'] + 1
                evidence['offline_teach'] = second
                audio = await client.get('/speak', params={'text':first['confirmation']})
                audio.raise_for_status()
                assert audio.content.startswith(b'RIFF')
                evidence['offline_cached_audio_bytes'] = len(audio.content)
                await call('POST', '/replay', {'action':'seek', 'ts':beats['unplug']['later_proof_seek_ts']})
                later = await until(match(beats['unplug']['later_matching_event']), timeout=10)
                assert not later['verdict']['show']
                evidence['offline_recurrence'] = later
                evidence['health'] = await call('GET','/health')
                assert evidence['health']['ok'] and not evidence['health']['online']
        finally:
            await call('POST','/replay', {'action':'pause'})
            evidence['undo'] = []
            for identifier in reversed(learned):
                evidence['undo'].append(await call('POST','/undo/'+identifier))
            await call('POST','/killswitch', {'on':not prior_online})
            evidence['remaining_rules'] = await call('GET','/rules')
        evidence['elapsed_seconds'] = round(perf_counter()-start, 3)
        evidence['success'] = True
        output = CONFIG.data.processed_dir / 'phase10_rehearsal.json'
        output.write_text(json.dumps(evidence, indent=2)+'\n')
        print(json.dumps({k:evidence[k] for k in ('success','elapsed_seconds','teach_ms','offline_cached_audio_bytes','remaining_rules')}, indent=2))
        print('Full evidence:', output)

if __name__ == '__main__':
    asyncio.run(main())
