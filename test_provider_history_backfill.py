import asyncio
from types import SimpleNamespace
import pytest
from provider_history_backfill import summarize,fetch_history


def row(key='123',ident='1',old='600000',new='550000'):
    return dict(HistoryTransactionalKey=ident,ResourceName='Property',ResourceRecordKey=key,FieldName='ListPrice',PreviousValue=old,NewValue=new)


def test_drop_count_handles_increases_invalid_values_and_duplicates_at_fetch():
    result=summarize([row(),row(old='550000',new='560000'),row(old='NaN'),row(old='missing')])
    assert result['recorded_drop_count']==1 and result['recorded_drop_total']==50000


def client(body):
    class Client:
        async def _access_token(self):return 'test'
    async def get(*args,**kwargs):return SimpleNamespace(raise_for_status=lambda:None,json=lambda:body)
    c=Client();c.http=SimpleNamespace(get=get);return c


def test_collection_deduplicates_provider_event_and_marks_coverage():
    result=asyncio.run(fetch_history(client({'value':[row(),row()]}),'123'))
    assert result['recorded_drop_count']==1 and result['pagination_complete']


def test_wrong_listing_is_rejected():
    with pytest.raises(ValueError,match='identity'):asyncio.run(fetch_history(client({'value':[row(key='456')]}),'123'))


def test_untrusted_pagination_is_rejected_before_request():
    with pytest.raises(ValueError,match='pagination'):asyncio.run(fetch_history(client({'value':[],'@odata.nextLink':'https://evil.test/trestle/odata/HistoryTransactional'}),'123'))
