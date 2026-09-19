"""Real Chrome regression against a running demo; requires optional Playwright."""
from __future__ import annotations
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from earshot.config import CONFIG


def main():
    output = CONFIG.data.processed_dir
    errors, requests, animations = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        context = browser.new_context(viewport={'width':1440,'height':1000})
        context.add_init_script('''
          window.__earshotSockets=[]; window.__earshotAnimations=[];
          const NativeWebSocket=window.WebSocket;
          window.WebSocket=class extends NativeWebSocket {
            constructor(...args){super(...args);window.__earshotSockets.push(this);}
          };
          document.addEventListener('animationstart', e=>window.__earshotAnimations.push({name:e.animationName,id:e.target.id}));
        ''')
        page = context.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('request', lambda r: requests.append(r.url))
        def api(method, path, data=None):
            response=context.request.fetch('http://127.0.0.1:8000'+path, method=method, data=data)
            assert response.ok, (path,response.status,response.text())
            return response.json()
        original_rules=api('GET','/rules')
        original_ids={r['rule_id'] for r in original_rules}
        prior_online=api('GET','/health')['online']
        evidence={}
        try:
            page.goto('http://127.0.0.1:8000/', wait_until='networkidle')
            expect(page.locator('#modelVersion')).not_to_have_text('v—')
            expect(page.locator('#alarmFeed .event').first).to_be_visible()
            expect(page.locator('#replayToggle')).to_be_enabled()
            page.locator('.scripted summary').click()
            page.locator('#soundEnabled').uncheck()
            page.locator('.scripted summary').click()
            evidence['initial_rate']=page.locator('#rateNumber').inner_text()
            evidence['initial_version']=page.locator('#modelVersion').inner_text()
            page.screenshot(path=str(output/'phase8_console_before.png'),full_page=True)
            page.locator('#teachText').fill('ignore generator cut-in on turbine four')
            page.locator('#teachText').press('Enter')
            expect(page.locator('#ruleLedger')).to_contain_text('ignore generator cut-in on turbine four')
            expect(page.locator('#modelVersion')).not_to_have_text(evidence['initial_version'])
            expect(page.locator('#alarmFeed .suppressed').first).to_be_visible()
            evidence['after_teach_version']=page.locator('#modelVersion').inner_text()
            evidence['suppressed_rows']=page.locator('#alarmFeed .suppressed').count()
            page.wait_for_function("window.__earshotAnimations.some(x=>x.id==='versionBadge')")
            evidence['animations']=page.evaluate("window.__earshotAnimations.filter(x=>x.id==='versionBadge')")
            assert any(x['id']=='versionBadge' for x in evidence['animations'])
            api('POST','/killswitch',{'on':False})
            expect(page.locator('#offlineToggle')).to_have_attribute('aria-checked','false')
            page.locator('#offlineToggle').click()
            expect(page.locator('#offlineToggle')).to_have_attribute('aria-checked','true')
            expect(page.locator('#linkText')).to_contain_text('OFFLINE')
            previous=page.locator('#modelVersion').inner_text()
            page.locator('#teachText').fill('ignore fast cut-out of generator on turbine four')
            page.locator('#teachText').press('Enter')
            expect(page.locator('#modelVersion')).not_to_have_text(previous)
            evidence['offline_teach_version']=page.locator('#modelVersion').inner_text()
            page.screenshot(path=str(output/'phase8_console_taught.png'),full_page=True)
            # Force one real socket disconnect; verify a new connection and state recovery.
            before=page.evaluate('window.__earshotSockets.length')
            page.evaluate("window.__earshotSockets.at(-1).close(1000,'regression reconnect')")
            page.wait_for_function('(before)=>window.__earshotSockets.length>before && window.__earshotSockets.at(-1).readyState===1', arg=before,timeout=15000)
            evidence['reconnected']=True
            # Explicitly labeled scripted fixture invokes the same durable teaching.
            before_version=api('GET','/health')['model_version']
            page.locator('.scripted summary').click()
            page.locator('#shortcuts button').nth(4).click()
            expect(page.locator('#ruleLedger')).to_contain_text('ignore generator cut-in on turbine seven')
            assert api('GET','/health')['model_version'] > before_version
            evidence['scripted_teach']=True
            # Contract test with mocked recognition: early recognition completion
            # must not apply a correction until the operator releases Space.
            voice_page=context.new_page()
            voice_page.add_init_script("""
              window.__mockEnded=false;
              window.SpeechRecognition=class {
                constructor(){this.processLocally=false;}
                static async available(){return 'available';}
                start(){setTimeout(()=>{
                  const result=[{transcript:'ignore the cable untwist alarm on turbine four'}];
                  result.isFinal=true; this.onresult({results:[result]});
                  window.__mockEnded=true;this.onend();
                },50);}
                stop(){this.onend();} abort(){this.onend();}
              };
            """)
            voice_page.goto('http://127.0.0.1:8000/',wait_until='networkidle')
            expect(voice_page.locator('#voiceTier')).to_contain_text('TIER 2')
            held_version=api('GET','/health')['model_version']
            voice_page.keyboard.down('Space')
            voice_page.wait_for_function('window.__mockEnded')
            assert api('GET','/health')['model_version']==held_version
            voice_page.keyboard.up('Space')
            expect(voice_page.locator('#ruleLedger')).to_contain_text('ignore the cable untwist alarm on turbine four')
            assert api('GET','/health')['model_version']==held_version+1
            evidence['mocked_local_speech_waits_for_release']=True
            voice_page.close()
            # Undo through the UI, not merely an API cleanup.
            first_card=page.locator('#ruleLedger .rule-card').filter(has_text='ignore generator cut-in on turbine four')
            first_card.get_by_role('button',name='Undo',exact=False).click()
            expect(first_card).to_have_count(0)
            evidence['ui_undo']=True
            page.locator('.scripted summary').click()
            page.set_viewport_size({'width':1366,'height':768})
            page.evaluate('window.scrollTo(0,0)')
            page.screenshot(path=str(output/'phase8_console_projector.png'),full_page=True)
            evidence['projector_boxes']={key:page.locator('#'+key).bounding_box() for key in ['linkPanel','versionBadge','ruleLedger','teachText']}
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(output/'phase8_console_mobile.png'),full_page=True)
            evidence['mobile_overflow']=page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert not evidence['mobile_overflow']
            ledger_box=evidence['projector_boxes']['ruleLedger']
            assert ledger_box['y']+min(ledger_box['height'],130)<768, 'Newest rule must fit the projector viewport'
            assert page.locator('#alarmFeed .event').count() <= 200
            assert not errors, errors
            external=[u for u in requests if urlparse(u).scheme in ('http','https','ws','wss') and urlparse(u).hostname not in ('127.0.0.1','localhost')]
            assert not external, external
            evidence.update(success=True,page_errors=errors,external_requests=external,requests=len(requests),browser=browser.version)
        finally:
            for rule in api('GET','/rules'):
                if rule['rule_id'] not in original_ids:
                    api('POST','/undo/'+rule['rule_id'])
            api('POST','/killswitch',{'on':not prior_online})
            browser.close()
        (output/'phase8_browser_validation.json').write_text(json.dumps(evidence,indent=2)+'\n')
        print(json.dumps(evidence,indent=2))

if __name__=='__main__':
    main()
