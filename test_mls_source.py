import pytest

from mls_source import (
    ExistingMLSSupabaseSource,
    canonical_digest,
    select_representative_media,
)


def test_source_rejects_any_project_except_approved_mls_supabase():
    with pytest.raises(ValueError,match="approved Supabase project"):
        ExistingMLSSupabaseSource(
            "postgresql://example.invalid/db",
            "https://wrong-project.supabase.co",
        )
    with pytest.raises(ValueError,match="database URL"):
        ExistingMLSSupabaseSource(
            "postgresql://postgres@wrong-pooler.example/db",
            "https://uxlfqsgynbbgspaakzey.supabase.co",
        )


def test_representative_media_is_bounded_deterministic_and_room_aware():
    media = [
        {"media_key":str(index),"order":index,"url":f"https://example.com/{index}.jpg",
         "short_description":description}
        for index,description in [
            (1,"Front exterior"),(2,"Living room"),(3,"Primary kitchen"),
            (4,"Bedroom"),(5,"Primary bathroom"),(6,"Rear exterior"),
            (7,"Dining room"),(8,"Hall"),(9,"Patio"),(10,"Garage"),
        ]
    ]
    first = select_representative_media(media)
    second = select_representative_media(list(reversed(media)))
    assert first==second
    assert len(first)==8
    assert [item["room"] for item in first[:2]]==["kitchen","bathroom"]
    assert len({item["media_key"] for item in first})==8


def test_canonical_digest_is_order_independent():
    assert canonical_digest({"a":1,"b":[2,3]})==canonical_digest({"b":[2,3],"a":1})


def test_bearer_token_can_only_be_sent_to_approved_cotality_media_url():
    ExistingMLSSupabaseSource._validate_source_url(
        "https://api.cotality.com/trestle/Media/Property/example",
    )
    with pytest.raises(ValueError,match="approved Cotality"):
        ExistingMLSSupabaseSource._validate_source_url(
            "https://example.com/trestle/Media/Property/example",
        )
