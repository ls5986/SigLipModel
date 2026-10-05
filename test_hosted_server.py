import http.client
import json
import os
import threading
from urllib.parse import urlencode

import pytest
from PIL import Image

from hosted_server import HostedAuth, create_server


class Studio:
    def __init__(self): self.saved=[]
    def get(self,path):
        if path.startswith('/api/studio/review-queue'):
            return {'items':[],'token':self.app.token}
        raise ValueError('missing')
    def post(self,path,payload):
        self.saved.append((path,payload));return {'revision':1}


class App:
    token='csrf'
    def __init__(self): self.studio=Studio();self.studio.app=self
    def get_studio(self): return self.studio


def auth(monkeypatch):
    monkeypatch.setenv('STUDIO_PUBLIC_ORIGIN','https://studio.example.test')
    monkeypatch.setenv('STUDIO_LOGIN_USERNAME','owner@example.test')
    monkeypatch.setenv('STUDIO_LOGIN_PASSWORD','correct horse battery')
    monkeypatch.setenv('STUDIO_SESSION_SECRET','s'*32)
    return HostedAuth()


def request(port,method,path,body=None,headers=None):
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
    headers={'Host':'studio.example.test',**(headers or {})}
    connection.request(method,path,body=body,headers=headers)
    response=connection.getresponse();data=response.read();result=(response.status,dict(response.getheaders()),data)
    connection.close();return result


def test_hosted_login_session_and_csrf(monkeypatch):
    app=App();server=create_server(0,app,auth(monkeypatch));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();port=server.server_address[1]
    try:
        status,_,body=request(port,'GET','/health')
        assert status==200 and json.loads(body)['version']=='unknown'
        status,headers,_=request(port,'GET','/')
        assert status==303 and headers['Location']=='/login'
        status,_,body=request(port,'GET','/api/studio/workbench/challenge/batches')
        assert status==401
        assert json.loads(body)=={
            'error':'Your session expired. Sign in again.','login':'/login',
        }
        form=urlencode({'username':'owner@example.test','password':'wrong'})
        assert request(port,'POST','/login',form,{'Origin':'https://studio.example.test','Content-Type':'application/x-www-form-urlencoded'})[0]==401
        form=urlencode({'username':'owner@example.test','password':'correct horse battery'})
        status,headers,_=request(port,'POST','/login',form,{'Origin':'https://studio.example.test','Content-Type':'application/x-www-form-urlencoded'})
        assert status==303
        cookie=headers['Set-Cookie'].split(';',1)[0]
        status,_,body=request(port,'GET','/',headers={'Cookie':cookie})
        assert status==200 and b'Your property training workspace' in body
        assert b'/api/studio/review-queue?scope=acquisitions&queue=all' in body
        assert b'No trained v2 fusion bundle is deployed' in body
        status,_,body=request(port,'GET','/mls-validation',headers={'Cookie':cookie})
        assert status==200 and b'Acquisition MLS Validation' in body
        assert request(port,'GET','/mls-validation')[0]==303
        status,_,body=request(port,'GET','/property-review',headers={'Cookie':cookie})
        assert status==200 and b'/status#models' in body and b'Training status' in body
        status,_,body=request(port,'GET','/workbench',headers={'Cookie':cookie})
        assert status==200 and b'Model Workbench' in body and b'TRAIN NEW CANDIDATE' in body
        status,headers,_=request(port,'GET','/?property=listing',headers={'Cookie':cookie})
        assert status==303 and headers['Location']=='/property-review?property=listing'
        status,_,body=request(port,'GET','/source-rows',headers={'Cookie':cookie})
        assert status==200 and b'All workbook rows' in body
        assert request(port,'GET','/source-rows')[0]==303
        status,_,body=request(port,'GET','/status',headers={'Cookie':cookie})
        assert status==200 and b'Training and data status' in body
        status,_,body=request(port,'GET','/api/studio/review-queue',headers={'Cookie':cookie})
        assert status==200 and json.loads(body)['token']=='csrf'
        payload=json.dumps({'answer':True})
        assert request(port,'POST','/api/studio/review',payload,{'Cookie':cookie,'Origin':'https://studio.example.test','Content-Type':'application/json'})[0]==403
        status,_,body=request(port,'POST','/api/studio/review',payload,{'Cookie':cookie,'Origin':'https://studio.example.test','X-Review-Token':'csrf','Content-Type':'application/json'})
        assert status==200 and json.loads(body)['revision']==1
    finally:
        server.shutdown();server.server_close();thread.join()


def test_review_ui_handles_html_api_responses_and_exposes_tagged_queue():
    page = open('review_ui.html', encoding='utf-8').read()
    assert 'value="tagged">Tagged photos ready' in page
    assert "contentType.includes('application/json')" in page
    assert "window.location.assign('/login')" in page
    assert 'response.json();' in page
    assert page.index("contentType.includes('application/json')") < page.index('response.json();')


def test_workbench_handles_expired_sessions_before_parsing_json():
    page = open('workbench_ui.html', encoding='utf-8').read()
    assert "redirect:'error'" in page
    assert "contentType.includes('application/json')" in page
    assert "window.location.assign(data.login||'/login')" in page
    assert page.index("contentType.includes('application/json')") < page.index('response.json();')


def test_authenticated_thumbnail_is_generated_without_studio_api_dispatch(monkeypatch,tmp_path):
    photo=tmp_path/'original.png'
    Image.new('RGB',(800,600),'navy').save(photo)
    app=App()
    app.studio.store=type('Store',(),{'image_path':lambda self,identifier:photo})()
    server=create_server(0,app,auth(monkeypatch));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();port=server.server_address[1]
    try:
        form=urlencode({'username':'owner@example.test','password':'correct horse battery'})
        status,headers,_=request(port,'POST','/login',form,{'Origin':'https://studio.example.test','Content-Type':'application/x-www-form-urlencoded'})
        assert status==303
        cookie=headers['Set-Cookie'].split(';',1)[0]
        status,headers,body=request(port,'GET','/api/studio/thumbnail?id=house%3Aphoto',headers={'Cookie':cookie})
        assert status==200 and headers['Content-Type']=='image/jpeg'
        output=tmp_path/'thumb.jpg';output.write_bytes(body)
        with Image.open(output) as image:
            assert image.width<=320 and image.height<=220
    finally:
        server.shutdown();server.server_close();thread.join()


def test_host_and_origin_are_pinned(monkeypatch):
    server=create_server(0,App(),auth(monkeypatch));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();port=server.server_address[1]
    try:
        assert request(port,'GET','/login',headers={'Host':'evil.example'})[0]==403
        form=urlencode({'username':'owner@example.test','password':'correct horse battery'})
        assert request(port,'POST','/login',form,{'Origin':'https://evil.example','Content-Type':'application/x-www-form-urlencoded'})[0]==403
    finally:
        server.shutdown();server.server_close();thread.join()


def test_same_origin_browser_fallback_when_origin_is_omitted(monkeypatch):
    app=App();server=create_server(0,app,auth(monkeypatch));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();port=server.server_address[1]
    try:
        form=urlencode({'username':'owner@example.test','password':'correct horse battery'})
        status,_,_=request(port,'POST','/login',form,{'Sec-Fetch-Site':'same-origin','Content-Type':'application/x-www-form-urlencoded'})
        assert status==303
        assert request(port,'POST','/login',form,{'Sec-Fetch-Site':'cross-site','Content-Type':'application/x-www-form-urlencoded'})[0]==403
    finally:
        server.shutdown();server.server_close();thread.join()


def test_hosted_origin_configuration_ignores_surrounding_whitespace(monkeypatch):
    monkeypatch.setenv('STUDIO_PUBLIC_ORIGIN','  https://studio.example.test/  ')
    monkeypatch.setenv('STUDIO_LOGIN_USERNAME','owner@example.test')
    monkeypatch.setenv('STUDIO_LOGIN_PASSWORD','correct horse battery')
    monkeypatch.setenv('STUDIO_SESSION_SECRET','s'*32)
    configured=HostedAuth()
    assert configured.origin=='https://studio.example.test'
    assert configured.host=='studio.example.test'


def test_hosted_auth_rejects_weak_or_non_https_configuration(monkeypatch):
    monkeypatch.setenv('STUDIO_PUBLIC_ORIGIN','http://studio.example.test')
    monkeypatch.setenv('STUDIO_LOGIN_USERNAME','owner@example.test')
    monkeypatch.setenv('STUDIO_LOGIN_PASSWORD','short')
    monkeypatch.setenv('STUDIO_SESSION_SECRET','short')
    with pytest.raises(ValueError): HostedAuth()
