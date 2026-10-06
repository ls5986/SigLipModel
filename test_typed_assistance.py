import copy
import json
from types import SimpleNamespace

import pytest

from condition_schema import TEXT_SIGNALS
from studio_v2 import label_evidence
from typed_dataset import preview
from typed_label_assistant import process, queue, result


class Store:
    def __init__(self):
        self.docs = {}
        self.detail = {"property":{"id":"listing", "mls_remarks":"Needs TLC", "metadata":{"YearBuilt":1960}},
            "images":[], "historical_source":{"blocked":False}}
    def document(self, key): return copy.deepcopy(self.docs.get(key))
    def property(self, identifier):
        value=copy.deepcopy(self.detail)
        value["property"]["id"]=identifier
        return value
    def save_document(self, key, value, revision):
        if (self.docs.get(key) or {}).get("revision", 0) != revision:
            raise RuntimeError("Stale revision")
        self.docs[key] = copy.deepcopy({**value, "revision":revision+1})
        return copy.deepcopy(self.docs[key])


def proposal():
    return {"physical_condition":"UNKNOWN", "modernization_state":"UNKNOWN", "acquisition_fit":"UNKNOWN",
        "reason":"Remarks do not establish severity or modernization", "text_signals":[
            {"signal":signal, "state":"PRESENT" if signal=="needs_tlc" else "UNKNOWN", "probability":None,
             "snippet":"Needs TLC" if signal=="needs_tlc" else None,
             "start":0 if signal=="needs_tlc" else None, "end":9 if signal=="needs_tlc" else None}
            for signal in TEXT_SIGNALS]}


def classifier(value, mutate=lambda:None):
    class Client:
        def post(self, url, **kwargs):
            mutate()
            # Fixture transport follows the actual provider quote-index format.
            raw=copy.deepcopy(value)
            inputs=json.loads(kwargs['json']['input'][1]['content'])
            quotes=inputs.get('evidence_quotes',[])
            if 'quote_index' in kwargs['json']['text']['format']['schema']['properties']['text_signals']['items'].get('properties',{}):
                tags=[]
                for item in raw['text_signals']:
                    if 'quote_index' in item:
                        tags.append(item);continue
                    if item['state']=='UNKNOWN':
                        index=None if all(item.get(k) is None for k in ('snippet','probability','start','end')) else -1
                    else:index=quotes.index(item['snippet']) if item.get('snippet') in quotes else -1
                    tags.append({'signal':item['signal'],'state':item['state'],'quote_index':index})
                raw['text_signals']=tags
            return SimpleNamespace(raise_for_status=lambda:None, json=lambda:{"status":"completed", "output":[
                {"type":"message", "content":[{"type":"output_text", "text":json.dumps(raw)}]}]})
    calls = []
    return SimpleNamespace(client=Client(), key="test-only", model="test-fixture", reserve_call=lambda:calls.append(1)), calls


def request(store):
    return queue(store, store.detail, {"confirmed":True, "label_evidence_id":label_evidence(
        "listing", "Needs TLC", [], {"YearBuilt":1960})}, "reviewer")


def test_real_transport_path_writes_only_machine_drafts_and_read_does_not_call():
    store = Store()
    req = request(store)
    assert request(store) == req  # duplicate queued request is idempotent
    provider, calls = classifier(proposal())
    assert process(store, req, provider)
    assert len(calls) == 1
    value = result(store, store.detail)["proposal"]
    assert value["status"] == "draft" and value["trained_v2"] is False
    assert "reviewer" not in value and value["requested_by"] == "reviewer"
    assert set(store.docs) == {"typed-label-request:listing", "typed-label-result:listing"}
    assert not process(store, store.document("typed-label-request:listing"), provider)
    assert len(calls) == 1
    store.detail["property"]["metadata"]["YearBuilt"] = 2026
    assert result(store, store.detail)["proposal"] is None


@pytest.mark.parametrize("change", [
    lambda value:value["text_signals"][0].update(state="ABSENT"),
    lambda value:value["text_signals"][9].update(snippet="Invented repair", end=15),
    lambda value:value["text_signals"].pop(),
    lambda value:value.update(physical_condition="target"),
])
def test_invalid_or_invented_evidence_fails_without_approval_or_automatic_retry(change):
    store = Store(); req = request(store); value = proposal(); change(value)
    provider, calls = classifier(value)
    assert not process(store, req, provider)
    assert store.document("typed-label-request:listing")["status"] == "failed"
    assert store.document("typed-label-result:listing") is None
    assert len(calls)==1


def test_evidence_change_during_paid_call_rejects_publication():
    store=Store();req=request(store)
    provider, _ = classifier(proposal(), lambda:store.detail["property"].update(mls_remarks="Changed"))
    assert not process(store, req, provider)
    assert store.document("typed-label-result:listing") is None


def test_quarantine_stale_requests_and_unconfirmed_writes_fail_before_payment():
    store=Store()
    with pytest.raises(ValueError, match="Confirm"):
        queue(store, store.detail, {}, "reviewer")
    with pytest.raises(RuntimeError, match="changed"):
        queue(store, store.detail, {"confirmed":True}, "reviewer")
    req=request(store);store.detail["historical_source"]["blocked"]=True
    provider,calls=classifier(proposal())
    assert not process(store, req, provider) and not calls


def row(store, id="listing", group="group", split="train"):
    detail=store.detail
    evidence=label_evidence(id, "Needs TLC", [], {"YearBuilt":1960})
    return {"id":id, "group_id":group, "split":split, "timing_verified":True,
        "metadata":detail["property"]["metadata"], "review":{
            "status":"approved", "reviewer":"human", "label_schema_version":"actvision-labels-v2",
            "label_evidence_id":evidence, "revision":1, "physical_condition":"C4_AVERAGE_FUNCTIONAL",
            "modernization_state":"ORIGINAL", "target_fit":"unsure", "text_signals":proposal()["text_signals"]}}


def test_preview_counts_independent_approved_truth_with_unknown_fit_and_missing_photos():
    store=Store(); first=row(store); second=row(store, id="duplicate", split="test")
    audit=preview(store,[first,second])
    assert audit["approved_review_rows"]==2
    assert audit["independent_groups"]=={"protected_test":1}
    assert audit["coverage"]["protected_test"]["acquisition_fit"]["UNKNOWN"]==1
    assert audit["coverage"]["protected_test"]["physical_condition"]["C4_AVERAGE_FUNCTIONAL"]==1
    assert audit["missing_modalities"]["photos"]==1 and not audit["excluded"]
    assert not audit["trainable"]
    before=audit["fingerprint"]
    first["review"]["physical_condition"]="UNKNOWN"
    updated=preview(store,[first,second])
    assert updated["fingerprint"]!=before and len(updated["conflicting_groups"])==1
    assert updated["independent_groups"]=={}


def test_cohort_and_unapproved_labels_never_become_typed_truth():
    store=Store(); p=row(store);p["known_target"]=True;p["review"]["status"]="draft"
    assert preview(store,[p])["approved_review_rows"]==0
    p["review"]["status"]="approved";p["review"]["label_schema_version"]="legacy"
    assert preview(store,[p])["approved_review_rows"]==0


@pytest.mark.parametrize("review,expected", [
    ({"revision":0,"legacy":False},0),
    ({"revision":0,"legacy":True,"target_fit":"target"},1),
    ({"revision":1,"legacy":False,"status":"draft"},1),
])
def test_preview_does_not_count_empty_imported_rows_as_reviews(review,expected):
    store=Store(); p=row(store);p["review"]=review
    audit=preview(store,[p])
    assert audit["draft_or_legacy_reviews"]==expected and audit["approved_review_rows"]==0


def test_v2_training_never_routes_to_legacy_target_classifier(monkeypatch):
    import studio_v2
    monkeypatch.setenv("STUDIO_ROLE", "operator")
    studio=SimpleNamespace(store=SimpleNamespace(database=True))
    for path in ("/api/studio/v2/train", "/api/studio/v2/dataset/freeze"):
        with pytest.raises(ValueError, match="legacy training"):
            studio_v2.post(studio,path,{"confirmed":True})


def test_assisted_approval_preserves_exact_proposal_and_rejects_replaced_draft(monkeypatch):
    import studio_v2
    store=Store(); req=request(store); provider,_=classifier(proposal());assert process(store,req,provider)
    detail=store.property("listing");detail["label_evidence_id"]=req["label_evidence_id"]
    monkeypatch.setattr(studio_v2,"get",lambda *args:detail)
    studio=SimpleNamespace(store=store,post=lambda path,value:value)
    proposed=store.document("typed-label-result:listing")
    payload={"id":"listing", "label_evidence_id":req["label_evidence_id"],
        "assistant_input_sha256":proposed["input_sha256"], "assistant_proposal_id":proposed["proposal_id"], "text_signals":[]}
    saved=studio_v2.post(studio,"/api/studio/v2/label",payload)
    assert saved["assistant_proposal"]["proposal_id"]==proposed["proposal_id"]
    assert saved["reviewer"]==studio_v2.identity()["id"]
    store.docs["typed-label-result:listing"]["proposal_id"]="replaced"
    with pytest.raises(RuntimeError,match="draft changed"):
        studio_v2.post(studio,"/api/studio/v2/label",payload)


def test_read_only_preview_available_to_authenticated_reviewers(monkeypatch):
    import studio_v2
    monkeypatch.setenv("STUDIO_ROLE","reviewer")
    monkeypatch.setattr("typed_dataset.preview",lambda store:{"read_only":True})
    studio=SimpleNamespace(store=SimpleNamespace(database=True))
    assert studio_v2.post(studio,"/api/studio/v2/dataset/preview",{})=={"read_only":True}


def test_unique_verbatim_snippet_offsets_are_derived_without_changing_evidence():
    store=Store(); req=request(store); value=proposal()
    signal=next(item for item in value["text_signals"] if item["signal"]=="needs_tlc")
    signal.update(start=1,end=10)
    provider,calls=classifier(value)
    assert process(store,req,provider) and len(calls)==1
    saved=store.document("typed-label-result:listing")
    signal=next(item for item in saved["text_signals"] if item["signal"]=="needs_tlc")
    assert signal["snippet"]=="Needs TLC" and signal["start"]==0 and signal["end"]==9
    assert saved["status"]=="draft" and saved["trained_v2"] is False


def test_ambiguous_and_invented_snippets_never_get_guessed_offsets():
    from typed_label_assistant import anchor_text_spans
    for remarks,snippet in [("Needs TLC. Needs TLC", "Needs TLC"),("Needs TLC", "Needs repair")]:
        item={"state":"PRESENT", "snippet":snippet, "start":99, "end":100}
        assert anchor_text_spans([item],remarks)[0]["start"]==99


def test_provider_failure_records_only_safe_stage_and_status():
    import httpx
    store=Store();req=request(store)
    response=httpx.Response(401,request=httpx.Request("POST","https://api.openai.com/v1/responses"))
    def fail(*args,**kwargs):
        raise httpx.HTTPStatusError("secret-provider-body-and-key",request=response.request,response=response)
    provider,_=classifier(proposal());provider.client.post=fail
    assert not process(store,req,provider)
    saved=store.document("typed-label-request:listing")
    assert saved["error_stage"]=="provider_request" and saved["provider_status"]==401
    assert saved["error_kind"]=="HTTPStatusError" and "secret-provider" not in json.dumps(saved)
    assert store.document("typed-label-result:listing") is None


def test_text_evidence_failure_reports_stage_without_private_evidence():
    store=Store();req=request(store);value=proposal()
    signal=next(item for item in value["text_signals"] if item["signal"]=="needs_tlc")
    signal["snippet"]="Private invented evidence"
    provider,_=classifier(value);assert not process(store,req,provider)
    saved=store.document("typed-label-request:listing")
    assert saved["error_stage"]=="structured_schema"
    assert saved["error_kind"]=="ValidationError"
    assert "Private invented evidence" not in json.dumps(saved)


def test_unknown_span_placeholders_are_rejected_with_safe_diagnostic_code():
    store=Store();req=request(store);value=proposal()
    signal=next(item for item in value["text_signals"] if item["state"]=="UNKNOWN")
    signal.update(snippet=None,start=0,end=0)
    provider,_=classifier(value);assert not process(store,req,provider)
    assert store.document("typed-label-request:listing")["error_stage"]=="structured_schema"
    assert store.document("typed-label-result:listing") is None


def test_supported_tags_cannot_claim_present_without_a_snippet():
    store=Store();req=request(store);value=proposal()
    signal=next(item for item in value["text_signals"] if item["signal"]=="needs_tlc")
    signal.update(snippet=None,start=None,end=None)
    provider,_=classifier(value);assert not process(store,req,provider)
    assert store.document("typed-label-request:listing")["error_stage"]=="structured_schema"
    assert store.document("typed-label-result:listing") is None


def test_constrained_quotes_preserve_unicode_and_derive_provider_null_offsets():
    from typed_label_assistant import evidence_quotes, schema, evidence
    from jsonschema import validate, ValidationError
    store=Store();store.detail['property']['mls_remarks']='A café. Needs TLC — original fixtures!'
    remarks=store.detail['property']['mls_remarks']
    quotes=evidence_quotes(remarks)
    assert quotes==['A café.', 'Needs TLC — original fixtures!']
    value=proposal()
    signal=next(i for i in value['text_signals'] if i['signal']=='needs_tlc')
    signal.update(snippet=quotes[1],start=None,end=None)
    validate(value,schema(remarks))
    req=queue(store,store.detail,{'confirmed':True,'label_evidence_id':evidence(store.detail)},'reviewer')
    provider,calls=classifier(value)
    assert process(store,req,provider) and len(calls)==1
    saved=store.document('typed-label-result:listing')
    tag=next(i for i in saved['text_signals'] if i['signal']=='needs_tlc')
    assert remarks[tag['start']:tag['end']]==quotes[1]
    assert saved['quotation_policy']=='source-quote-index-v2'
    signal['snippet']='Needs TLC - original fixtures!'
    with pytest.raises(ValidationError):validate(value,schema(remarks))


def test_empty_remarks_force_all_description_signals_unknown():
    from typed_label_assistant import schema
    from jsonschema import validate, ValidationError
    value=proposal()
    with pytest.raises(ValidationError):validate(value,schema(''))
    for item in value['text_signals']:
        item.update(state='UNKNOWN',snippet=None,start=None,end=None)
    validate(value,schema(''))


def test_repeated_source_sentences_use_unique_context_without_guessing():
    from typed_label_assistant import evidence_quotes,anchor_text_spans
    remarks='Needs TLC. Needs TLC.'
    quotes=evidence_quotes(remarks)
    assert quotes==[remarks]
    item={'state':'PRESENT','snippet':quotes[0],'start':None,'end':None}
    assert anchor_text_spans([item],remarks)[0]['end']==len(remarks)


def test_newline_separated_source_quotes_are_not_lost():
    from typed_label_assistant import evidence_quotes
    assert evidence_quotes("Needs TLC\nOriginal fixtures") == ["Needs TLC", "Original fixtures"]


def test_provider_diagnostic_never_saves_response_text_or_unrecognized_codes():
    import httpx
    from typed_label_assistant import provider_diagnostic
    req=httpx.Request('POST','https://api.openai.com/v1/responses')
    response=httpx.Response(400,request=req,json={'error':{'code':'private-source-text','message':'Invalid schema grammar for private listing text and secret-key'}})
    error=httpx.HTTPStatusError('secret-body',request=req,response=response)
    result=provider_diagnostic(error)
    assert result=={'code':'provider_rejection','hints':['schema','grammar','invalid']}
    assert 'secret' not in json.dumps(result) and 'private' not in json.dumps(result)


def test_provider_union_discriminator_is_the_first_property_and_has_disjoint_values():
    from typed_label_assistant import schema
    branches=schema('Needs TLC')['properties']['text_signals']['items']['anyOf']
    assert all(next(iter(branch['properties']))=='state' for branch in branches)
    assert set(branches[0]['properties']['state']['enum']).isdisjoint(branches[1]['properties']['state']['enum'])


def test_provider_quote_index_schema_has_no_source_string_enum_or_union():
    from typed_label_assistant import provider_schema,expand_quote_references
    from jsonschema import validate,ValidationError
    remarks='Needs TLC. Original fixtures!'
    response=provider_schema(remarks)
    assert 'anyOf' not in json.dumps(response)
    assert 'Needs TLC' not in json.dumps(response)
    value=proposal()
    value['text_signals']=[{'signal':signal,'state':'PRESENT' if signal=='needs_tlc' else 'UNKNOWN','quote_index':0 if signal=='needs_tlc' else None} for signal in TEXT_SIGNALS]
    validate(value,response)
    expanded=expand_quote_references(value,remarks)
    supported=next(i for i in expanded['text_signals'] if i['signal']=='needs_tlc')
    assert supported['snippet']=='Needs TLC.' and supported['probability'] is None
    value['text_signals'][0]['quote_index']=999
    with pytest.raises(ValidationError):validate(value,response)
    value['text_signals'][0]['quote_index']=0
    with pytest.raises(ValueError,match='UNKNOWN'):expand_quote_references(value,remarks)
