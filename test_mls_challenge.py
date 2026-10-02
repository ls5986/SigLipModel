import copy

import pytest

import model_workbench as module
from model_workbench import (
    challenge_properties, freeze_challenge_batch, save_feedback,
)


class Result:
    def __init__(self, rows):
        self.rows=rows
    def fetchall(self):
        return copy.deepcopy(self.rows)


class Database:
    class Connection:
        def __init__(self, rows):
            self.rows=rows
        def __enter__(self):
            return self
        def __exit__(self,*args):
            return None
        def execute(self,query,params):
            assert "acq_training.examples" in query
            return Result(self.rows)
    def __init__(self, keys):
        self.rows=[{"listing_key":key} for key in keys]
    def connect(self):
        return self.Connection(self.rows)


class Store:
    def __init__(self, keys=()):
        self.workspace="test"
        self.database=Database(keys)
        self.docs={}
    def save_document(self,key,value,expected):
        current=self.docs.get(key)
        assert (current or {}).get("revision",0)==expected
        self.docs[key]={**value,"revision":expected+1}
        return copy.deepcopy(self.docs[key])


def listing(key):
    return {
        "id":"mls:"+key,"listing_key":key,"address":"Example "+key,
        "city":"San Diego","metadata":{"ListingKey":key,"YearBuilt":1960},
        "metadata_sha256":"m"*64,
        "media":[{
            "media_key":"photo-1","room":"kitchen",
            "source_url":"https://example.invalid/photo.jpg",
        }],
        "opportunity":{"score":80},
        "source":"existing-mls-supabase-read-only",
    }


class Source:
    def __init__(self):
        self.items={"new":listing("new"),"known":listing("known")}
        self.excluded=None
    def list_active(self,**kwargs):
        self.excluded=set(kwargs["exclude"])
        return [self.items["new"]]
    def random_active(self,**kwargs):
        return self.list_active(**kwargs)
    def current_opportunities(self,**kwargs):
        return self.list_active(**kwargs)
    def listings(self,keys):
        return [self.items[key] for key in keys if key in self.items]
    def immutable_snapshot(self,item):
        return {
            "listing_key":item["listing_key"],
            "metadata":item["metadata"],
            "metadata_sha256":"a"*64,
            "photos":[{"media_key":"photo-1","sha256":"b"*64,"bytes":100}],
            "photo_manifest_sha256":"c"*64,
            "source":"existing-mls-supabase-read-only",
        }


def test_challenge_browse_excludes_dataset_and_hides_source_urls():
    store,source=Store(["known"]),Source()
    result=challenge_properties(store,source,{"mode":"active","limit":5})
    assert source.excluded=={"known"}
    assert result["items"][0]["listing_key"]=="new"
    assert "source_url" not in result["items"][0]["images"][0]
    assert "ground truth" in result["notice"]


def test_fixed_challenge_is_immutable_and_rejects_dataset_overlap():
    store,source=Store(["known"]),Source()
    with pytest.raises(ValueError,match="overlaps"):
        freeze_challenge_batch(store,source,{
            "name":"Invalid","listing_keys":["known"],
        })
    batch=freeze_challenge_batch(store,source,{
        "name":"Random five","listing_keys":["new"],"source_mode":"random",
    })
    assert batch["status"]=="frozen"
    assert len(batch["fingerprint"])==64
    assert batch["counts"]=={"properties":1,"photos":1,"metadata_only":0}
    assert store.docs["workbench-challenge-batch:"+batch["id"]]["fingerprint"]==batch["fingerprint"]


def test_challenge_feedback_is_isolated_unless_explicitly_promoted(monkeypatch):
    store=Store()
    state={
        "request":{
            "status":"completed",
            "property_id":"challenge:"+"c"*32+":new",
        },
        "result":{"model":{"version":"v0"},"photos":[]},
        "feedback":None,
    }
    monkeypatch.setattr(module,"run_status",lambda store,identifier:state)
    base={
        "run_id":"a"*32,"target_label":"NOT_TARGET","reviewer":"R",
        "physical_condition":"UNKNOWN","modernization_state":"UNKNOWN",
    }
    saved=save_feedback(store,base)
    assert saved["challenge_only"] is True
    assert saved["promoted_to_dataset"] is False
    state["feedback"]=saved
    promoted=save_feedback(store,{**base,"promote_to_dataset":True})
    assert promoted["challenge_only"] is False
    assert promoted["promoted_to_dataset"] is True
    state["feedback"]=promoted
    with pytest.raises(ValueError,match="UNKNOWN"):
        save_feedback(store,{
            **base,"target_label":"UNKNOWN","promote_to_dataset":True,
        })
