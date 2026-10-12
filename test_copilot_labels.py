import json
from types import SimpleNamespace as NS

import pytest
pytest.importorskip("copilot")
from PIL import Image
from copilot_labels import CopilotLabels, CopilotTransport
from pilot import FEATURES


class Store:
    def __init__(self): self.docs={}
    def document(self,k): return self.docs.get(k)
    def save_document(self,k,v,revision):
        self.docs[k]={**v,'revision':revision+1}; return self.docs[k]


class Session:
    def __init__(self): self.sent=[]; self.disconnected=False
    async def send_and_wait(self,prompt,**kwargs):
        self.sent.append((prompt,kwargs))
        return NS(data=NS(content=json.dumps({'images':[{'index':0,'context':'subject',
            'condition_label':'maintained_original','features':dict.fromkeys(FEATURES,None),'uncertain':False}]})))
    async def disconnect(self): self.disconnected=True


class Client:
    def __init__(self): self.sessions=[]; self.options=[]
    async def start(self): pass
    async def stop(self): pass
    async def get_auth_status(self): return NS(isAuthenticated=True)
    async def list_models(self): return [NS(id='gpt-4.1',policy=None,capabilities=NS(supports=NS(vision=True),limits=NS(vision=None)))]
    async def create_session(self,**kwargs):
        self.options.append(kwargs); session=Session(); self.sessions.append(session); return session


def test_sdk_uses_schema_images_no_tools_and_no_openai_key(tmp_path,monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.delenv('STUDIO_COPILOT_MODEL',raising=False)
    path=tmp_path/'photo.jpg'; Image.new('RGB',(20,20)).save(path)
    client=Client(); transport=CopilotTransport(client); labels=CopilotLabels(Store(),transport)
    try:
        assert transport.preflight()==['gpt-4.1']
        rows=labels.classify([path],[{'room':'kitchen','context':'subject'}])
        assert rows[0]['room']=='kitchen' and rows[0]['provider']=='copilot'
        assert rows[0]['context_source']=='GitHub Copilot draft'
        assert client.options[0]['available_tools']==[]
        assert client.options[0]['on_permission_request'](None).kind=='reject'
        sent=client.sessions[0].sent[0][1]
        assert sent['attachments'][0]['mimeType']=='image/jpeg'
        assert sent['attachments'][0]['type']=='blob'
        json.dumps(sent['attachments'])  # SDK sends these directly over JSON-RPC.
        assert 'room' not in sent['response_schema']['properties']['images']['items']['properties']
        assert client.sessions[0].disconnected
        labels.classify([path],[{'room':'kitchen','context':'subject'}])
        assert len(client.sessions)==1  # Exact-photo/model/room cache prevents another billable call.
    finally: labels.close()


def test_sdk_no_login_fails_before_inference():
    class NoLogin(Client):
        async def get_auth_status(self): return NS(isAuthenticated=False)
    transport=CopilotTransport(NoLogin())
    try:
        with pytest.raises(ValueError,match='not signed in'): transport.preflight()
        assert not transport.client.sessions
    finally: transport.close()
