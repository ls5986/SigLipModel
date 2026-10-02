"""Local GitHub Copilot SDK image drafts with isolated, tool-free sessions."""
import asyncio
import json
import os
import tempfile

import httpx
from openai_labels import OpenAILabels, hybrid_policy


class CopilotTransport:
    def __init__(self, client=None):
        self.workspace = tempfile.TemporaryDirectory(prefix="siglip-photo-tags-")
        self.loop = asyncio.new_event_loop()
        if client is None:
            from copilot import CopilotClient
            client = CopilotClient(working_directory=self.workspace.name)
        self.client = client
        self.model = os.environ.get('STUDIO_COPILOT_MODEL','gpt-4.1')
        self.started = False

    async def check(self):
        if not self.started:
            await self.client.start(); self.started = True
        auth = await self.client.get_auth_status()
        if not auth.isAuthenticated:
            raise ValueError('Copilot CLI is not signed in. Run copilot and /login with your own GitHub account.')
        models = await self.client.list_models()
        usable = [m for m in models if m.capabilities.supports.vision and
                  (m.policy is None or m.policy.state=='enabled')]
        selected = next((m for m in usable if m.id==self.model),None)
        if selected is None:
            raise ValueError('Set STUDIO_COPILOT_MODEL to an available vision model: '+', '.join(m.id for m in usable))
        limits = selected.capabilities.limits.vision
        if limits and limits.max_prompt_images is not None and limits.max_prompt_images < 4:
            raise ValueError('Choose a vision model supporting at least four images per request')
        self.max_bytes = limits.max_prompt_image_size if limits else None
        return [m.id for m in usable]

    def preflight(self): return self.loop.run_until_complete(self.check())

    async def _post(self, body):
        from copilot.generated.session_events import AttachmentBlob
        from copilot.generated.rpc import PermissionDecisionReject
        if not self.started: await self.check()
        attachments = []; prompt = [body['input'][0]['content']]
        for part in body['input'][1]['content']:
            if part['type']=='input_text': prompt.append(part['text'])
            elif part['type']=='input_image':
                data = part['image_url'].split(',',1)[1]
                import base64
                if self.max_bytes and len(base64.b64decode(data))>self.max_bytes:
                    raise ValueError('Photo exceeds selected model image limit')
                attachments.append(AttachmentBlob(mime_type='image/jpeg',data=data,
                                                  display_name='photo-'+str(len(attachments))+'.jpg'))
        session = await self.client.create_session(model=self.model, available_tools=[], working_directory=self.workspace.name,
            on_permission_request=lambda *args: PermissionDecisionReject(feedback='Image tagging only; no tools permitted'))
        try:
            result = await session.send_and_wait('\n'.join(prompt),attachments=attachments,
                response_schema=body['text']['format']['schema'],timeout=120)
            if result is None: raise ValueError('No structured image labels returned')
            output = result.data.content
            # Validate JSON here; the shared classifier also validates exact image indices and tags.
            json.loads(output)
            return httpx.Response(200,json={'status':'completed','output':[{'type':'message',
                'content':[{'type':'output_text','text':output}]}]},request=httpx.Request('POST','http://local-copilot'))
        finally:
            await session.disconnect()

    def post(self, url, *, headers, json):
        # This adapter never sends an HTTP request or forwards an OpenAI key.
        return self.loop.run_until_complete(self._post(json))

    def close(self):
        try:
            if self.started: self.loop.run_until_complete(self.client.stop())
        finally:
            self.loop.close()
            self.workspace.cleanup()


class CopilotLabels(OpenAILabels):
    provider = 'copilot'
    stage = 'features'
    paid = True

    def __init__(self, store, transport=None):
        self.transport = transport or CopilotTransport()
        super().__init__(store,client=self.transport,require_key=False)
        self.model = self.transport.model
        # Outer queue policy stays compatible with SigLIP rooms; cache is provider/model-specific.
        self.policy = hybrid_policy()
        self.cache_policy = 'siglip-rooms-copilot-features-v1:'+self.model
        self.budget_provider = 'copilot'
        self.limit = int(os.environ.get('STUDIO_COPILOT_MAX_CALLS_PER_DAY','10000'))
        if not 1<=self.limit<=10000: raise ValueError('Invalid Copilot daily call limit')

    def classify(self, paths, room_tags=None):
        if room_tags is None: raise ValueError('SigLIP room tags required before Copilot labeling')
        try: rows = super().classify(paths,room_tags)
        except ValueError:
            raise ValueError('Copilot draft labeling failed; check CLI login, model access and credits before retrying') from None
        return [{**row,'context_source':'GitHub Copilot draft','provider':'copilot',
                 'provenance':'GitHub Copilot image-only draft; human approval required'} for row in rows]
