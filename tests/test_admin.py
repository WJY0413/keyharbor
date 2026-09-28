import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from cooper_litellm.admin import Admin
from fixtures import registry


class AdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.registry = root / 'registry.json'
        self.registry.write_text(json.dumps(registry()))
        self.codex = root / 'config.toml'; self.codex.write_text('model="native-test"\n')
        self.admin = Admin(self.registry, root / 'runtime', self.codex)
        async def health(): return {'ready':True,'keys':{},'models':['beta-gpt-6-luna']}
        self.admin.health = health
        self.client = TestClient(TestServer(self.admin.application(), host='127.0.0.1'))
        await self.client.start_server()
        self.admin.port = self.client.port

    async def asyncTearDown(self):
        await self.client.close(); self.temp.cleanup()

    async def test_state_never_exposes_credentials(self):
        r = await self.client.get('/api/state'); data = await r.json()
        self.assertEqual(r.status,200)
        for key in data['profiles']:
            self.assertNotIn('secret_file',key); self.assertNotIn('secret',key)
        self.assertIn('frame-ancestors',r.headers['Content-Security-Policy'])

    async def test_csrf_cross_origin_and_host(self):
        self.assertEqual((await self.client.post('/api/reload',json={})).status,403)
        self.assertEqual((await self.client.post('/api/reload',json={},headers={'X-CSRF-Token':self.admin.csrf,'Origin':'https://evil.example'})).status,403)
        self.assertEqual((await self.client.get('/',headers={'Host':'evil.example'})).status,403)

    async def test_visibility_save_preserves_alpha_and_secret(self):
        original = json.loads(self.registry.read_text())
        r = await self.client.post('/api/profile',json={'id':'beta','visible':['gpt-6-luna'],'revision':self.admin.revision()},headers={'X-CSRF-Token':self.admin.csrf})
        self.assertEqual(r.status,200)
        actual = json.loads(self.registry.read_text())
        self.assertEqual(actual['keys'][0],original['keys'][0])
        self.assertEqual(actual['keys'][1]['secret_file'],original['keys'][1]['secret_file'])
        self.assertEqual(actual['keys'][1]['visible'],['gpt-6-luna'])

    async def test_stale_edit_rejected(self):
        r = await self.client.post('/api/profile',json={'id':'beta','visible':[],'revision':'old'},headers={'X-CSRF-Token':self.admin.csrf})
        self.assertEqual(r.status,409)

    async def test_invalid_import_never_saved(self):
        before = self.registry.read_bytes()
        r = await self.client.post('/api/profile',json={'id':'new','prefix':'new-','api_base':'http://bad.example','secret':'test-not-real','revision':self.admin.revision()},headers={'X-CSRF-Token':self.admin.csrf})
        self.assertEqual(r.status,400);self.assertEqual(self.registry.read_bytes(),before)

    async def test_soft_remove_leaves_other_profile(self):
        r = await self.client.post('/api/remove',json={'id':'beta','revision':self.admin.revision()},headers={'X-CSRF-Token':self.admin.csrf})
        self.assertEqual(r.status,200)
        self.assertEqual([k['id'] for k in json.loads(self.registry.read_text())['keys']],['alpha'])

    async def test_import_encrypts_new_slot(self):
        async def discover(manager,key,secret): return [object()], 'test-version'
        with patch('cooper_litellm.runtime.Manager.discover',discover):
            r = await self.client.post('/api/profile',json={'id':'supplier-c','display_name':'备用供应商 C','prefix':'supplier-c-','default_mode':'chat','api_base':'https://upstream.example/v1','secret':'test-not-real','revision':self.admin.revision()},headers={'X-CSRF-Token':self.admin.csrf})
        self.assertEqual(r.status,200)
        cfg = json.loads(self.registry.read_text()); profile = cfg['keys'][-1]
        self.assertEqual(len(cfg['keys']),3)
        self.assertEqual(profile['id'],'supplier-c')
        self.assertEqual(profile['display_name'],'备用供应商 C')
        self.assertEqual(profile['default_mode'],'chat')
        self.assertEqual(profile['visible'],[])
        self.assertNotIn('test-not-real',self.registry.read_text())
        self.assertNotIn(b'test-not-real',Path(profile['secret_file']).read_bytes())


if __name__ == '__main__': unittest.main()
