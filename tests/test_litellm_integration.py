"""Real LiteLLM subprocesses against a loopback fake upstream; no production keys."""
import asyncio
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from cooper_litellm.gateway import app
from cooper_litellm.runtime import Manager, Route


class RealLiteLLMTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_fallback_hot_reload_and_inflight_drain(self):
        entered, release = asyncio.Event(), asyncio.Event()
        upstream_keys = []
        async def respond(request):
            data = await request.json()
            auth = request.headers.get('Authorization','')
            upstream_keys.append(auth)
            if auth == 'Bearer fake-alpha':
                return web.json_response({'error':{'message':'test refusal','type':'invalid_api_key','code':'invalid_api_key'}},status=401)
            if auth == 'Bearer fake-beta':
                return web.json_response({'error':{'message':'test rate limit','type':'rate_limit','code':'rate_limit'}},status=429)
            if data.get('input') == 'hold':
                entered.set()
                await release.wait()
            return web.json_response({'id':'resp_fake','object':'response','created_at':1,'status':'completed',
                                      'model':'test','output':[{'type':'message','id':'msg_fake','status':'completed','role':'assistant',
                                      'content':[{'type':'output_text','text':'OK','annotations':[]}]}],
                                      'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}})
        upstream_app = web.Application(); upstream_app.router.add_post('/v1/responses',respond)
        upstream = TestServer(upstream_app,host='127.0.0.1'); await upstream.start_server()
        with tempfile.TemporaryDirectory() as folder:
            manager = Manager('unused',folder,secret_loader=lambda path:'fake-'+path)
            async def discover(key,secret):
                alias = key['prefix']+'test'
                return [Route(alias,key['id'],'test','responses','internal_'+key['id'],False,secret)], secret
            manager.discover = discover
            cfg = {'schema':1,'port':4103,'keys':[
                {'id':'alpha','prefix':'alpha-','secret_file':'alpha','api_base':str(upstream.make_url('/v1')),'fallback_keys':['beta','gamma']},
                {'id':'beta','prefix':'beta-','secret_file':'beta','api_base':str(upstream.make_url('/v1'))},
                {'id':'gamma','prefix':'gamma-','secret_file':'gamma','api_base':str(upstream.make_url('/v1'))}]}
            client = TestClient(TestServer(app(manager),host='127.0.0.1'))
            await client.start_server()
            try:
                with patch('cooper_litellm.runtime.config.load',return_value=cfg):
                    self.assertTrue(await manager.refresh(),manager.last_error)
                response = await client.post('/v1/responses',json={'model':'alpha-test','input':'hello'})
                self.assertEqual(response.status,200); await response.read()
                self.assertEqual(response.headers['X-Cooper-Key'],'gamma')
                self.assertEqual(upstream_keys,['Bearer fake-alpha','Bearer fake-beta','Bearer fake-gamma'])
                old = manager.current
                held = asyncio.create_task(client.post('/v1/responses',json={'model':'gamma-test','input':'hold'}))
                await asyncio.wait_for(entered.wait(),10)
                next_cfg = copy.deepcopy(cfg);next_cfg['refresh_seconds']=301
                with patch('cooper_litellm.runtime.config.load',return_value=next_cfg):
                    self.assertTrue(await manager.refresh(),manager.last_error)
                self.assertNotEqual(manager.current.id,old.id)
                self.assertEqual(old.inflight,1);self.assertIsNone(old.process.poll())
                release.set();r = await asyncio.wait_for(held,10);self.assertEqual(r.status,200);await r.read()
                for _ in range(30):
                    if old.process.poll() is not None:break
                    await asyncio.sleep(.1)
                self.assertIsNotNone(old.process.poll())
            finally:
                release.set();await client.close();await manager.close();await upstream.close()


if __name__ == '__main__': unittest.main()
