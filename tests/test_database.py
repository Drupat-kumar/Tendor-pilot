from tenderpilot.database import (
    initialize_database,
    list_saved_tenders,
    list_tenders,
    load_profile,
    save_profile,
    save_tender,
    upsert_tender,
)
from tenderpilot.models import ContractorProfile, Tender


def test_profile_tender_deduplication_and_saved_state(tmp_path):
    path = tmp_path / "test.sqlite3"
    initialize_database(path)
    profile = ContractorProfile(company_name="Example Works", services=["Road paving"])
    save_profile(profile, path)
    assert load_profile(path).company_name == "Example Works"

    tender = Tender(
        tender_id="NIT/123",
        title="Road paving services",
        source_id="cppp",
        source_name="CPPP",
        source_url="https://eprocure.gov.in/notice",
    )
    upsert_tender(tender, path)
    upsert_tender(tender, path)
    assert len(list_tenders(path)) == 1
    save_tender(tender.dedupe_key, True, path)
    assert len(list_saved_tenders(path)) == 1

    tender.title = "Updated road paving services"
    upsert_tender(tender, path)
    assert list_tenders(path)[0].saved is True
    save_tender(tender.dedupe_key, False, path)
    assert list_saved_tenders(path) == []


def test_same_tender_id_on_different_sources_is_not_deduplicated(tmp_path):
    path = tmp_path / "test.sqlite3"
    initialize_database(path)
    for source_id, source_name, url in [
        ("cppp", "CPPP", "https://eprocure.gov.in/notice/123"),
        ("ireps", "IREPS", "https://ireps.gov.in/notice/123"),
    ]:
        upsert_tender(Tender(
            tender_id="123",
            title="Tender",
            source_id=source_id,
            source_name=source_name,
            source_url=url,
        ), path)
    assert len(list_tenders(path)) == 2
