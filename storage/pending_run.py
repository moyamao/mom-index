"""Private recovery files for failed collector transactions (never web-served)."""
import fcntl
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import SimpleNamespace
from contextlib import contextmanager

from storage import mysql_store

PENDING = Path(__file__).resolve().parents[1] / "private" / "pending_runs"


@contextmanager
def _original_identity(identity):
    values = {"MOM_INDEX_LLM_PROFILE": identity["profile"], "MOM_INDEX_LLM_MODEL": identity["model_name"],
              "MOM_INDEX_LLM_PROMPT_VERSION": identity["prompt_version"], "MOM_INDEX_RUNTIME_ROLE": identity["runtime_role"]}
    old = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, val in old.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val


def save_pending_run(note, posts, results, sectors, dashboard, source_run_id):
    PENDING.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = PENDING / (note.split(":")[-1] + ".json")
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=PENDING, delete=False) as handle:
        json.dump({"note": note, "posts": posts, "results": {
            s: [vars(item) for item in items] for s, items in results.items()
        }, "sectors": sectors, "dashboard": dashboard, "source_run_id": source_run_id},
            handle, ensure_ascii=False, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(handle.name, path)
    return path


def replay(path):
    if not mysql_store.mysql_enabled():
        raise RuntimeError("恢复入库需要启用 MySQL")
    with path.open(encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        job = json.load(handle)
        # A broken connection during COMMIT does not prove that MySQL rolled back.
        conn = mysql_store._connect()
        try:
            with conn.cursor() as cur:
                mysql_store._ensure_tables(cur)
                cur.execute("SELECT id FROM mom_index_analysis_batches WHERE note=%s AND status='completed' LIMIT 1",
                            (job["note"],))
                committed = cur.fetchone()
        finally:
            conn.close()
        if not committed:
            identity = job["dashboard"]["analysis_identity"]
            results = {s: [SimpleNamespace(**r) for r in rows] for s, rows in job["results"].items()}
            with _original_identity(identity):
                if job["source_run_id"] is not None:
                    mysql_store.persist_standalone_analysis(results, job["posts"], job["note"])
                else:
                    mysql_store.persist_pipeline_run(job["posts"], results, job["sectors"], job["dashboard"], job["note"])
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    for pending in sorted(PENDING.glob("*.json")):
        replay(pending)
        print(f"已确认入库：{pending.name}")
