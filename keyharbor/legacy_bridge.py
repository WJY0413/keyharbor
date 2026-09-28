"""Compatibility listener for clients requiring the older router health schema.

All inference is delegated to KeyHarbor. No upstream credentials or independent
model catalogue are stored in this process.
"""
import json
from pathlib import Path

import httpx
from aiohttp import web

HOP = {'host','connection','content-length','transfer-encoding','keep-alive','upgrade','proxy-authorization'}


def application(backend, port, client=None):
    session = client or httpx.AsyncClient(timeout=httpx.Timeout(None,connect=10,write=60,pool=30),trust_env=False,follow_redirects=False)
    async def handle(request):
        if request.host not in (f'127.0.0.1:{port}',f'localhost:{port}') or request.headers.get('Origin'):
            return web.json_response({'error':'local_clients_only'},status=403)
        if request.path == '/healthz' and request.method == 'GET':
            try:
                response = await session.get(backend+'/healthz',timeout=3)
                status = response.json()
                if response.status_code != 200 or status.get('service') != 'cooper-litellm' or not status.get('ready'):
                    raise ValueError('backend_not_ready')
            except (httpx.HTTPError,ValueError):
                return web.json_response({'service':'private-additive-router','ready':False,'implementation':'keyharbor'},status=503)
            return web.json_response({**status,'service':'private-additive-router','implementation':'keyharbor','backend':backend})
        if not request.path.startswith('/v1/'):
            return web.json_response({'error':'unknown_route'},status=404)
        headers = {k:v for k,v in request.headers.items() if k.lower() not in HOP}
        upstream = None
        prepared = False
        try:
            req = session.build_request(request.method,backend+request.path,params=request.query,
                                        headers=headers,content=await request.read())
            upstream = await session.send(req,stream=True)
            out = web.StreamResponse(status=upstream.status_code,headers={k:v for k,v in upstream.headers.items() if k.lower() not in HOP})
            await out.prepare(request); prepared = True
            async for chunk in upstream.aiter_raw(): await out.write(chunk)
            await out.write_eof()
            return out
        except httpx.HTTPError:
            if prepared: raise ConnectionResetError('backend_stream_interrupted') from None
            return web.json_response({'error':'keyharbor_unavailable'},status=503)
        finally:
            if upstream is not None: await upstream.aclose()
    async def close(app): await session.aclose()
    app = web.Application(client_max_size=32*1024*1024)
    app.router.add_route('*','/{path:.*}',handle)
    app.on_cleanup.append(close)
    return app


def run(registry,port=4105):
    cfg = json.loads(Path(registry).read_text(encoding='utf-8-sig'))
    backend = f"http://127.0.0.1:{int(cfg.get('port',4103))}"
    if port == int(cfg.get('port',4103)): raise ValueError('Bridge must use a separate port')
    web.run_app(application(backend,port),host='127.0.0.1',port=port,access_log=None,print=None,auto_decompress=False)
