import http.client
import json
import os
import threading
from urllib.parse import urlencode

import pytest

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
        assert request(port,'GET','/health')[0]==200
        status,headers,_=request(port,'GET','/')
        assert status==303 and headers['Location']=='/login'
        form=urlencode({'username':'owner@example.test','password':'wrong'})
        assert request(port,'POST','/login',form,{'Origin':'https://studio.example.test','Content-Type':'application/x-www-form-urlencoded'})[0]==401
        form=urlencode({'username':'owner@example.test','password':'correct horse battery'})
        status,headers,_=request(port,'POST','/login',form,{'Origin':'https://studio.example.test','Content-Type':'application/x-www-form-urlencoded'})
        assert status==303
        cookie=headers['Set-Cookie'].split(';',1)[0]
        status,_,body=request(port,'GET','/api/studio/review-queue',headers={'Cookie':cookie})
        assert status==200 and json.loads(body)['token']=='csrf'
        payload=json.dumps({'answer':True})
        assert request(port,'POST','/api/studio/review',payload,{'Cookie':cookie,'Origin':'https://studio.example.test','Content-Type':'application/json'})[0]==403
        status,_,body=request(port,'POST','/api/studio/review',payload,{'Cookie':cookie,'Origin':'https://studio.example.test','X-Review-Token':'csrf','Content-Type':'application/json'})
        assert status==200 and json.loads(body)['revision']==1
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


def test_hosted_auth_rejects_weak_or_non_https_configuration(monkeypatch):
    monkeypatch.setenv('STUDIO_PUBLIC_ORIGIN','http://studio.example.test')
    monkeypatch.setenv('STUDIO_LOGIN_USERNAME','owner@example.test')
    monkeypatch.setenv('STUDIO_LOGIN_PASSWORD','short')
    monkeypatch.setenv('STUDIO_SESSION_SECRET','short')
    with pytest.raises(ValueError): HostedAuth()
