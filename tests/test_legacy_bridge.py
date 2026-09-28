import json
import unittest
import httpx
from aiohttp.test_utils import TestClient,TestServer
from keyharbor.legacy_bridge import application


class RawStream(httpx.AsyncByteStream):
    async def __aiter__(self): yield b'data: {"type":"response.completed"}\n\n'


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests=[]
        self.respond=lambda req:httpx.Response(200,json={'service':'cooper-litellm','ready':True,'models':['custom-test']})
        async def transport(req):
            self.requests.append(req);return self.respond(req)
        self.upstream=httpx.AsyncClient(transport=httpx.MockTransport(transport))
        # Set the Host header explicitly: the synthetic server chooses a random port.
        self.client=TestClient(TestServer(application('http://127.0.0.1:4103',4105,self.upstream)))
        await self.client.start_server()
        self.headers={'Host':'127.0.0.1:4105'}
    async def asyncTearDown(self): await self.client.close()
    async def test_legacy_health_is_dynamic(self):
        result=await self.client.get('/healthz',headers=self.headers)
        data=await result.json()
        self.assertEqual(data['service'],'private-additive-router')
        self.assertEqual(data['models'],['custom-test'])
        self.assertEqual(data['implementation'],'keyharbor')
    async def test_down_backend_never_advertises_healthy(self):
        self.respond=lambda req:httpx.Response(200,json={'service':'cooper-litellm','ready':False})
        self.assertEqual((await self.client.get('/healthz',headers=self.headers)).status,503)
    async def test_stream_and_auth_delegate_only_to_loopback(self):
        self.respond=lambda req:httpx.Response(200,headers={'Content-Type':'text/event-stream'},stream=RawStream())
        result=await self.client.post('/v1/responses',json={'model':'custom-test','input':'hello'},headers={**self.headers,'Authorization':'Bearer synthetic-test'})
        self.assertIn('response.completed',await result.text())
        self.assertEqual(str(self.requests[-1].url),'http://127.0.0.1:4103/v1/responses')
        self.assertEqual(self.requests[-1].headers['Authorization'],'Bearer synthetic-test')
    async def test_cross_origin_rejected(self):
        self.assertEqual((await self.client.get('/healthz',headers={**self.headers,'Origin':'https://foreign.example'})).status,403)
