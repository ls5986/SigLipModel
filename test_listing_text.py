from listing_text import image_evidence, phrase_report
from photo_selection import selection


def test_disclosure_blocks_even_manual_include():
    evidence = image_evidence({'PublicRemarks':'Some photographs are virtually staged.'})
    assert evidence['excluded']
    assert not selection('subject',{'include_in_similarity':True},evidence)['included']


def test_photo_caption_disclosure_and_no_substring_false_positive():
    assert image_evidence({},'AI-generated interior')['excluded']
    assert not image_evidence({'PublicRemarks':'Stairs, airy rooms and a virtual tour.'})['excluded']


def test_keyword_counts_are_per_listing_and_do_not_use_prices():
    report = phrase_report([
        {'ListingKey':'a','PublicRemarks':'Original kitchen, original kitchen. Asking $850,000.'},
        {'ListingKey':'a','PublicRemarks':'Original kitchen'},
        {'ListingKey':'b','PublicRemarks':'Original kitchen. Virtually staged.'}])
    assert report['listings']==2
    assert {'phrase':'original kitchen','listings':2} in report['phrases']
    assert report['synthetic_disclosures'][0]['listing_key']=='b'
    assert not any('850' in row['phrase'] for row in report['phrases'])
