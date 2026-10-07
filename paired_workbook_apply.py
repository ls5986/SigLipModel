"""Operator-only staged import. Does not freeze, deploy, train, or call an MLS API."""
import argparse,json,os
from pathlib import Path
from paired_workbook_import import read_workbook,prepare

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--workbook',required=True)
 parser.add_argument('--stored-evidence',nargs='+',required=True)
 parser.add_argument('--schema-directory',default=str(Path(__file__).parent.parent/'sql'))
 parser.add_argument('--apply',action='store_true',help='Apply staging writes to the explicitly supplied training connection')
 parser.add_argument('--expected-project-ref',required=True)
 args=parser.parse_args();source=read_workbook(args.workbook)
 stored=[]
 for path in args.stored_evidence:stored.extend(json.loads(Path(path).read_text()))
 rows=prepare(source,stored)
 if not args.apply:
  print(json.dumps({'mode':'dry_run','rows':len(rows),'source_sha256':source['sha256']}));return
 import psycopg
 from psycopg.types.json import Jsonb
 url=os.environ.get('ACQ_TRAINING_DATABASE_URL','')
 if not url or args.expected_project_ref not in url:raise SystemExit('An explicit training connection matching the project ref is required')
 statements=(Path(args.schema_directory)/'paired_workbook_ingest.sql').read_text().replace(':payload','%(payload)s').replace(':source_hash','%(source_hash)s').split(';')
 with psycopg.connect(url) as db:
  # Never silently create/migrate a schema or overwrite an import. Operator applies reviewed DDL first.
  workspaces=db.execute('SELECT DISTINCT workspace_id FROM acq_training.property_groups').fetchall()
  if len(workspaces)!=1:raise ValueError('Explicit workspace mapping required for a multi-workspace database')
  ws=workspaces[0][0]
  db.execute('''INSERT INTO acq_training.source_imports(workspace_id,source_name,source_sha256,schema_version,row_count,status)
   VALUES(%s,%s,%s,'paired-workbook-v1',%s,'staged') ON CONFLICT DO NOTHING''',(ws,source['source_name'],source['sha256'],len(rows)))
  db.commit()
  for start in range(0,len(rows),6):
   with db.transaction():
    params={'payload':Jsonb(rows[start:start+6]),'source_hash':source['sha256']}
    for stmt in statements:
     if stmt.strip():db.execute(stmt,params)
  count=db.execute('''SELECT count(*) FROM acq_training.source_rows s JOIN acq_training.source_imports i ON i.workspace_id=s.workspace_id AND i.id=s.source_import_id WHERE i.workspace_id=%s AND i.source_sha256=%s''',(ws,source['sha256'])).fetchone()[0]
  if count!=len(rows):raise ValueError('Source/database row reconciliation failed')
 print(json.dumps({'mode':'staged','rows':count,'training_started':False,'frozen':False}))
if __name__=='__main__':main()
