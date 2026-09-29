"""Migrate records to the approved destination only; photos are metadata until Storage upload."""

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

import psycopg
from migration_config import HISTORICAL_ROOT, PILOT, PROJECT, require_project
from psycopg.types.json import Jsonb

HERE = Path(__file__).resolve().parent
FULL = HISTORICAL_ROOT / "full_cohort"
WORKSPACE = uuid5(UUID("ce7004bc-6db4-43bd-ac85-b6c59fd21a21"), "acq-vision-pilot")


def uid(kind, value):
    return uuid5(WORKSPACE, kind + ":" + str(value))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def connection():
    require_project()
    password = os.environ.get("PGPASSWORD")
    if not password:
        raise ValueError("PGPASSWORD must be supplied in process memory")
    host = os.environ.get("SUPABASE_DB_HOST")
    if not host:
        raise ValueError("Set SUPABASE_DB_HOST to the destination pooler hostname")
    conn = psycopg.connect(
        host=host, port=int(os.environ.get("SUPABASE_DB_PORT", "5432")), dbname="postgres",
        user="postgres." + PROJECT, password=password, sslmode="require",
        connect_timeout=15, autocommit=True,
    )
    if not conn.pgconn.ssl_in_use:
        conn.close()
        raise ValueError("Encrypted client connection required")
    return conn


def schema(conn, validate_only):
    sql = (HERE / "supabase_training_schema.sql").read_text()
    sql = sql.replace("begin;\n", "", 1).rsplit("commit;", 1)[0]
    with conn.transaction(force_rollback=validate_only):
        conn.execute("SET LOCAL statement_timeout=60000")
        exists = conn.execute("select to_regnamespace('acq_training')").fetchone()[0]
        if not exists:
            conn.execute(sql)
        tables = {row[0] for row in conn.execute(
            "select tablename from pg_tables where schemaname='acq_training'"
        ).fetchall()}
        required = {"principals", "property_groups", "examples", "photos", "review_events",
                    "datasets", "dataset_groups", "dataset_items", "model_runs", "predictions"}
        if not required <= tables:
            raise ValueError("Destination schema is incomplete; refusing implicit repair")
        for table in required - {"principals"}:
            enabled = conn.execute(
                "select relrowsecurity from pg_class where oid=%s::regclass",
                ("acq_training." + table,),
            ).fetchone()[0]
            if not enabled:
                raise ValueError("Training table missing row-level protection")
        conn.execute("INSERT INTO acq_training.principals VALUES (session_user,%s) ON CONFLICT DO NOTHING",
                     (WORKSPACE,))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS acq_training.migration_documents(
              workspace_id uuid NOT NULL, logical_path text NOT NULL, sha256 text NOT NULL,
              payload jsonb NOT NULL, migrated_at timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY(workspace_id,logical_path,sha256));
            CREATE TABLE IF NOT EXISTS acq_training.migration_receipts(
              workspace_id uuid NOT NULL, source_sha256 text NOT NULL,
              record_counts jsonb NOT NULL, storage_uploaded boolean NOT NULL DEFAULT false,
              verified_at timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY(workspace_id,source_sha256));
            REVOKE ALL ON acq_training.migration_documents,acq_training.migration_receipts
              FROM public,anon,authenticated;
            ALTER TABLE acq_training.migration_documents ENABLE ROW LEVEL SECURITY;
            ALTER TABLE acq_training.migration_receipts ENABLE ROW LEVEL SECURITY;
        """)
        # Exercise the actual restricted role; empty principals must not expose rows.
        conn.execute("GRANT acq_training_reader TO postgres")
        conn.execute("SET LOCAL ROLE acq_training_reader")
        conn.execute("SELECT count(*) FROM acq_training.examples").fetchone()
        blocked = False
        try:
            with conn.transaction():
                conn.execute("DELETE FROM acq_training.examples WHERE false")
        except psycopg.errors.InsufficientPrivilege:
            blocked = True
        if not blocked:
            raise ValueError("Read role unexpectedly has delete access")
        conn.execute("RESET ROLE")
        conn.execute("REVOKE acq_training_reader FROM postgres")
    return {"schema_validation": "passed", "rolled_back": validate_only}


def migrate(conn):
    source = read_json(FULL / "input_records.json")
    evidence = read_json(FULL / "sample_evidence.json")
    if not evidence.get("finished_at"):
        raise ValueError("Collection must finish before migration")
    if source["source_sha256"] != evidence["source_sha256"]:
        raise ValueError("Evidence and workbook identities differ")
    by_row = {row["source_row"]: row for row in evidence["properties"]}
    local = sqlite3.connect(FULL / "training_catalog.sqlite3")
    local.row_factory = sqlite3.Row
    documents = [PILOT / "data" / "human_reviews.json", PILOT / "data" / "manifest.json",
                 PILOT / "artifacts" / "candidate_latest.json",
                 PILOT / "artifacts" / "studio_candidate_latest.json",
                 PILOT / "artifacts" / "backbone.json", HISTORICAL_ROOT / "training_policy.json",
                 FULL / "input_records.json", FULL / "sample_evidence.json"]
    try:
        with conn.transaction(), conn.pipeline() as pipeline:
            conn.execute("SET LOCAL statement_timeout=60000")
            for group in local.execute("SELECT * FROM property_groups"):
                conn.execute("""
                    INSERT INTO acq_training.property_groups(workspace_id,id,identity_key,identity_verified,protected_test)
                    VALUES (%s,%s,%s,false,%s) ON CONFLICT (workspace_id,id) DO NOTHING
                """, (WORKSPACE, uid("group", group["group_key"]), group["group_key"], bool(group["protected_test"])))
            for example in local.execute("SELECT * FROM examples"):
                facts = json.loads(example["source_snapshot"])
                live = by_row[example["source_row"]]
                key = live.get("candidate_listing_key")
                chosen = next((r["listing"] for r in live["candidates"] if r["listing"]["ListingKey"] == key), {})
                transaction = {k: v for k, v in facts.items() if "Sale" in k}
                conn.execute("""
                    INSERT INTO acq_training.examples
                      (workspace_id,id,group_id,provider,listing_key,listing_id,source_workbook_sha256,source_rows,
                       target_transaction,source_snapshot,snapshot_at,match_status,photo_era,reference_label,reference_origin)
                    VALUES (%s,%s,%s,'Trestle',%s,%s,%s,%s,%s,%s,%s,%s,%s,'GOOD EXAMPLE',%s)
                    ON CONFLICT (workspace_id,id) DO NOTHING
                """, (WORKSPACE, uid("example", example["id"]), uid("group", example["group_key"]),
                      key, chosen.get("ListingId"), source["source_sha256"], [example["source_row"]],
                      Jsonb(transaction), Jsonb({"spreadsheet": facts, "mls_candidates": live["candidates"]}),
                      evidence["updated_at"], "candidate" if key else "unresolved",
                      "acquisition_candidate" if key else "unknown", "User-provided property cohort; not gold or per-photo labels"))
            photo_count = 0
            pipeline.sync()
            print(json.dumps({"phase": "records_staged", "examples": 618}), flush=True)
            for example in local.execute("SELECT id,listing_key FROM examples WHERE listing_key IS NOT NULL"):
                for photo in local.execute("SELECT * FROM photos WHERE listing_key=?", (example["listing_key"],)).fetchall():
                    metadata = json.loads(photo["metadata"])
                    object_key = "photos/" + photo["sha256"] + Path(photo["local_path"]).suffix
                    identifier = uid("photo", example["id"] + ":" + photo["media_key"] + ":" + photo["sha256"])
                    conn.execute("""
                        INSERT INTO acq_training.photos
                          (workspace_id,id,example_id,provider_media_key,image_sha256,storage_bucket,
                           storage_object_key,provider_modified_at,retrieved_at,rights_reference,context,context_evidence)
                        VALUES (%s,%s,%s,%s,%s,'acq-training-private',%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (workspace_id,id) DO NOTHING
                    """, (WORKSPACE, identifier, uid("example", example["id"]), photo["media_key"], photo["sha256"],
                          object_key, metadata.get("MediaModificationTimestamp") or metadata.get("ModificationTimestamp"),
                          evidence["updated_at"], "Existing authorized MLS retrieval; retention/training rights not independently verified",
                          "shared_amenity" if photo["context_status"] == "provider_described_shared_amenity" else "unknown",
                          Jsonb({"provider_metadata": metadata, "storage_upload_status": "pending",
                                 "training_approved": False})))
                    photo_count += 1
                    if photo_count % 200 == 0:
                        pipeline.sync()
            for path in documents:
                payload = read_json(path)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                conn.execute("""
                    INSERT INTO acq_training.migration_documents(workspace_id,logical_path,sha256,payload)
                    VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING
                """, (WORKSPACE, str(path.relative_to(PILOT)), digest, Jsonb(payload)))
            # Preserve Studio-only labels and every revision independently of legacy JSON.
            studio = sqlite3.connect(PILOT / "data" / "studio" / "studio.sqlite3")
            studio.row_factory = sqlite3.Row
            try:
                for table in ("reviews", "review_history"):
                    payload = [dict(row) for row in studio.execute("SELECT * FROM " + table)]
                    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
                    conn.execute("""
                        INSERT INTO acq_training.migration_documents(workspace_id,logical_path,sha256,payload)
                        VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING
                    """, (WORKSPACE, "studio.sqlite3:" + table, digest, Jsonb(payload)))
            finally:
                studio.close()
            counts = {
                table: conn.execute("SELECT count(*) FROM acq_training." + table + " WHERE workspace_id=%s",
                                    (WORKSPACE,)).fetchone()[0]
                for table in ("property_groups", "examples", "photos", "migration_documents")
            }
            if counts["property_groups"] != 551 or counts["examples"] != 618 or counts["photos"] != photo_count:
                raise ValueError("Destination record counts do not match; rolling back")
            legacy = read_json(PILOT / "data" / "human_reviews.json")
            restored = conn.execute(
                "SELECT payload FROM acq_training.migration_documents WHERE workspace_id=%s AND logical_path=%s ORDER BY migrated_at DESC LIMIT 1",
                (WORKSPACE, str((PILOT / "data" / "human_reviews.json").relative_to(PILOT))),
            ).fetchone()[0]
            if restored != legacy:
                raise ValueError("Restored review history does not match; rolling back")
            conn.execute("""
                INSERT INTO acq_training.migration_receipts(workspace_id,source_sha256,record_counts,storage_uploaded)
                VALUES (%s,%s,%s,false) ON CONFLICT (workspace_id,source_sha256)
                DO UPDATE SET record_counts=excluded.record_counts,verified_at=now()
            """, (WORKSPACE, source["source_sha256"], Jsonb(counts)))
        result = {"project": PROJECT, "workspace": str(WORKSPACE), "counts": counts,
                  "review_history_roundtrip_verified": True, "storage_uploaded": False,
                  "studio_cutover": False, "local_files_deleted": False,
                  "verified_at": datetime.now(UTC).isoformat()}
        (FULL / "database_migration_receipt.json").write_text(json.dumps(result, indent=2))
        return result
    finally:
        local.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["validate", "migrate"])
    args = parser.parse_args()
    with connection() as conn:
        print(json.dumps(schema(conn, args.operation == "validate")), flush=True)
        if args.operation == "migrate":
            print(json.dumps(migrate(conn)), flush=True)
