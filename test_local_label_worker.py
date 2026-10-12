from local_label_worker import enqueue_all


def test_full_enqueue_is_paged_and_skips_blocked_unavailable_or_running():
    class Store:
        def __init__(self): self.offsets=[];self.requested=[]
        def queue(self,args):
            self.offsets.append(args['offset'])
            assert args['scope']=='acquisitions'
            if args['offset']==0:
                return {'total':42,'items':[{'id':str(i),'image_count':1,'blocked':i==0} for i in range(40)]}
            return {'total':42,'items':[{'id':'40','image_count':0},{'id':'41','image_count':1}]}
        def document(self,key): return {'status':'running'} if key.endswith(':1') else {'status':'queued'} if key.endswith(':2') else {}
        def request_autolabel(self,payload):
            self.requested.append(payload)
            assert payload['all_photos'] and payload['label_provider']=='copilot'
            return {'status':'completed' if payload['property_id']=='41' else 'queued'}
    store=Store();result=enqueue_all(store)
    assert store.offsets==[0,40]
    assert result=={'queued':38,'completed':1,'already_active':1,'skipped':2}
    assert any(r['property_id']=='2' for r in store.requested)  # Existing queued rooms are upgraded.
