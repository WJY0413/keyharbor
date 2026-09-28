import asyncio
import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import tomlkit
from aiohttp.test_utils import TestClient, TestServer

from cooper_litellm import config, secrets
from cooper_litellm.gateway import Gateway, app, pin_id, restore_input
from cooper_litellm.native import switch, watchdog
from cooper_litellm.runtime import Generation, Manager, Route
from cooper_litellm.state import KeyState
from fixtures import registry


class Process:
    def __init__(self): self.dead = False
    def poll(self): return 0 if self.dead else None
    def terminate(self): self.dead = True
    def wait(self, timeout=None): return 0


def generation():
    routes = {
        'alpha-test': Route('alpha-test', 'alpha', 'test', 'responses', 'internal_f', True, 'v1'),
        'beta-test': Route('beta-test', 'beta', 'test', 'responses', 'internal_j', False, 'v2'),
    }
    registry = {'keys': [{'id': 'alpha', 'prefix': 'alpha-', 'fallback_keys': ['beta']}, {'id': 'beta', 'prefix': 'beta-'}]}
    return Generation('g1', 'http://127.0.0.1:49999/v1', 'internal-secret', Process(), routes, registry, 's1')


class BasicTests(unittest.TestCase):
    def test_classification(self):
        s = KeyState(); s.failure(401); self.assertFalse(s.available()); self.assertEqual(s.status, 'invalid')
        s.success(); s.failure(429, now=100, retry_after=10)
        self.assertFalse(s.available(109)); self.assertTrue(s.available(111))
        s.success(); s.failure(429, 'insufficient_quota'); self.assertEqual(s.status, 'exhausted')
        s.success(); s.failure(403); self.assertTrue(s.available())
        for _ in range(3): s.failure(502)
        self.assertEqual(s.status, 'cooldown')

    def test_dpapi_isolation(self):
        if os.name != 'nt': self.skipTest('Windows only')
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / 'a', Path(d) / 'b'
            secrets.save(a, 'test-not-real-a'); secrets.save(b, 'test-not-real-b')
            self.assertEqual(secrets.load(a), 'test-not-real-a')
            with self.assertRaises(FileExistsError): secrets.save(a, 'overwrite')
            secrets.save(b, 'rotated', replace=True)
            self.assertEqual(secrets.load(a), 'test-not-real-a')
            self.assertNotIn(b'test-not-real', a.read_bytes())

    def test_native_escape_hatch_and_backup(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
            p = Path(d) / 'config.toml'
            original = 'model="native-test"\nmodel_provider="openai"\nopenai_base_url="http://127.0.0.1:4103/v1"\nmodel_catalog_json="static.json"\n[mcp_servers.keep]\nurl="https://example.com"\n'
            p.write_text(original)
            result = switch(p, 'native', backup_root=Path(d) / 'backups', registry=Path(d)/'profiles.json')
            doc = tomlkit.parse(p.read_text())
            self.assertNotIn('openai_base_url', doc); self.assertNotIn('model_catalog_json', doc)
            self.assertEqual(doc['mcp_servers']['keep']['url'], 'https://example.com')
            self.assertEqual((Path(result['backup']) / 'config.toml').read_text(), original)
            self.assertFalse(switch(p, 'native', backup_root=Path(d) / 'backups', registry=Path(d)/'profiles.json')['changed'])

    def test_watchdog_dead_gateway(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True), patch('urllib.request.urlopen', side_effect=OSError):
            p = Path(d) / 'config.toml'
            p.write_text('model="native-test"\nopenai_base_url="http://127.0.0.1:4103/v1"\n')
            result = watchdog(p, 4103, Path(d) / 'backups', interval=0, failures=3, registry=Path(d)/'profiles.json')
            self.assertEqual(result['mode'], 'native')

    def test_duplicate_secret_slot_rejected(self):
        cfg = registry()
        cfg['keys'][1]['secret_file'] = cfg['keys'][0]['secret_file']
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'registry.json'; p.write_text(json.dumps(cfg))
            with self.assertRaises(ValueError): config.load(p)

    def test_foreign_compaction_rejected(self):
        with self.assertRaises(ValueError): restore_input([{'type':'compaction','encrypted_content':'other'}], 'alpha-')

    def test_native_guard_uses_configured_prefix(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
            path = Path(d)/'config.toml'; path.write_text('model="custom-vendor-model"\n')
            profiles = Path(d)/'registry.json'
            profiles.write_text(json.dumps({'keys':[{'prefix':'custom-vendor-'}]}))
            with self.assertRaisesRegex(ValueError,'official native default'):
                switch(path,'native',backup_root=Path(d)/'backup',registry=profiles)
            self.assertEqual(path.read_text(),'model="custom-vendor-model"\n')


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.requests = []
        self.handler = lambda req: httpx.Response(200, json={'id':'resp_test','output':[]})
        async def transport(req):
            self.requests.append(req)
            return self.handler(req)
        self.registry = Path(self.temp.name) / 'registry.json'
        self.registry.write_text(json.dumps(registry()))
        self.manager = Manager(self.registry, self.temp.name, client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
        self.manager.current = generation()
        self.manager.states = {'alpha':KeyState(), 'beta':KeyState()}
        self.client = TestClient(TestServer(app(self.manager), host='127.0.0.1'))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close(); await self.manager.close(); self.temp.cleanup()

    async def test_private_auth_stripped_and_fallback(self):
        def handler(req):
            body = json.loads(req.content)
            self.assertEqual(req.headers['authorization'], 'Bearer internal-secret')
            self.assertNotIn('chatgpt-account-id', req.headers)
            if body['model'] == 'internal_f': return httpx.Response(401, json={'error':{'code':'invalid_api_key'}})
            return httpx.Response(200, json={'id':'resp_j','output':[]})
        self.handler = handler
        result = await self.client.post('/v1/responses', json={'model':'alpha-test','input':'hello','prompt_cache_key':'session'}, headers={'Authorization':'Bearer native-secret','ChatGPT-Account-ID':'private-account'})
        self.assertEqual(result.status, 200); self.assertEqual(result.headers['X-Cooper-Key'], 'beta')
        self.assertEqual(self.manager.states['alpha'].status, 'invalid')
        self.assertEqual(self.manager.pins[pin_id('session:session')][0], 'beta')

    async def test_native_passthrough(self):
        result = await self.client.post('/v1/responses', json={'model':'native-test','input':'hello'}, headers={'Authorization':'Bearer native-secret'})
        self.assertEqual(result.status, 200)
        self.assertEqual(self.requests[0].url.host, 'chatgpt.com')
        self.assertEqual(self.requests[0].headers['authorization'], 'Bearer native-secret')

    async def test_no_replay_on_uncertain_error(self):
        self.handler = lambda req: httpx.Response(503, json={'error':{}})
        result = await self.client.post('/v1/responses', json={'model':'alpha-test','input':'hello'})
        self.assertEqual(result.status, 503); self.assertEqual(len(self.requests), 1)

    async def test_affinity_unknown_response_and_rotation(self):
        gateway = Gateway(self.manager); gen = self.manager.current; route = gen.routes['alpha-test']
        gateway.remember({'prompt_cache_key':'session'}, route, 'known')
        with self.assertRaises(ValueError): gateway.candidates(gen, route, {'prompt_cache_key':'session','previous_response_id':'unknown'})
        gen.routes['alpha-test'] = Route('alpha-test','alpha','test','responses','internal_f',True,'rotated')
        self.assertEqual(gateway.candidates(gen, gen.routes['alpha-test'], {'prompt_cache_key':'session'}), [])

    async def test_dynamic_official_and_hidden_beta(self):
        self.handler = lambda req: httpx.Response(200, json={'models':[{'slug':'test','display_name':'official'}]})
        result = await self.client.get('/v1/models')
        self.assertEqual([m['slug'] for m in (await result.json())['models']], ['test','alpha-test'])
        self.handler = lambda req: httpx.Response(200, json={'models':[{'slug':'new-official'}]})
        result = await self.client.get('/v1/models')
        self.assertEqual([m['slug'] for m in (await result.json())['models']], ['new-official','alpha-test'])
        self.handler = lambda req: httpx.Response(401)
        self.assertEqual((await self.client.get('/v1/models')).status, 401)

    async def test_atomic_reload_drains_and_rejection_preserves(self):
        old = self.manager.current; old.inflight = 1
        new = generation(); new.id = 'g2'
        async def build(cfg): return new
        with patch.object(self.manager, 'build', build): self.assertTrue(await self.manager.refresh())
        self.assertIs(self.manager.current, new); self.assertFalse(old.process.dead)
        old.inflight = 0; await self.manager.reap(); self.assertTrue(old.process.dead)
        async def fail(cfg): raise ValueError('bad candidate')
        with patch.object(self.manager, 'build', fail): self.assertFalse(await self.manager.refresh())
        self.assertIs(self.manager.current, new); self.assertFalse(new.process.dead)

    async def test_browser_origin_rejected(self):
        self.assertEqual((await self.client.get('/healthz', headers={'Origin':'https://evil.example'})).status, 403)

    async def test_disable_all_clears_private_routes(self):
        cfg = registry()
        for key in cfg['keys']: key['enabled'] = False
        candidate = await self.manager.build(cfg)
        self.assertEqual(candidate.routes, {})
        self.assertIsNone(candidate.process.poll())

    async def test_custom_profile_discovery_with_explicit_protocol(self):
        self.handler = lambda req: httpx.Response(200,json={'data':[{'id':'vendor-model'}]})
        key = {'id':'supplier-c','prefix':'supplier-c-','api_base':'https://vendor.example/v1',
               'default_mode':'chat','visible':['vendor-model']}
        routes,_ = await self.manager.discover(key,'fake-test-key')
        self.assertEqual([(r.alias,r.key,r.mode,r.visible) for r in routes],
                         [('supplier-c-vendor-model','supplier-c','chat',True)])

    async def test_third_profile_visibility_not_name_dependent(self):
        gen = self.manager.current
        gen.routes['supplier-c-test'] = Route('supplier-c-test','supplier-c','test','responses','internal_c',True,'v3')
        self.manager.states['supplier-c'] = KeyState()
        self.handler = lambda req: httpx.Response(200,json={'models':[{'slug':'test'}]})
        response = await self.client.get('/v1/models')
        names = {row['slug'] for row in (await response.json())['models']}
        self.assertIn('supplier-c-test',names)
        gen.routes['supplier-c-test'].visible = False
        response = await self.client.get('/v1/models')
        self.assertNotIn('supplier-c-test',{row['slug'] for row in (await response.json())['models']})


if __name__ == '__main__': unittest.main()
