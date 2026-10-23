from __future__ import annotations

import random
import shutil
import sqlite3
import subprocess
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

import pytest

from photo_search.api import random_browse
from photo_search.api.errors import ApiError
from photo_search.api.queries import random_asset_summaries
from photo_search.api.random_browse import sample_asset_ids, validate_ids


class MiniCatalog:
    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.query_log: list[str] = []
        self.connection.executescript(
            """
            CREATE TABLE assets(asset_id INTEGER PRIMARY KEY, sha256 TEXT, byte_size INTEGER, created_at INTEGER);
            CREATE TABLE source_roots(source_id INTEGER PRIMARY KEY, display_name TEXT);
            CREATE TABLE file_locations(location_id INTEGER PRIMARY KEY, asset_id INTEGER, source_id INTEGER,
              relative_path TEXT, availability TEXT, size INTEGER, mtime_ns INTEGER);
            CREATE INDEX idx_file_locations_asset ON file_locations(asset_id);
            CREATE INDEX idx_file_locations_present_asset ON file_locations(asset_id, location_id)
              WHERE availability='PRESENT' AND asset_id IS NOT NULL;
            CREATE INDEX idx_file_locations_present_source_asset ON file_locations(source_id, asset_id, location_id)
              WHERE availability='PRESENT' AND asset_id IS NOT NULL;
            """
        )
        self.connection.executemany("INSERT INTO source_roots VALUES (?,?)", [(1, "一号图库"), (2, "二号图库")])
        for asset_id in range(1, 651):
            digest = f"{asset_id:064x}"
            self.connection.execute("INSERT INTO assets VALUES (?,?,?,?)", (asset_id, digest, 123, asset_id))
            self.connection.execute("INSERT INTO file_locations VALUES (?,?,?,?,?,?,?)",
                (asset_id, asset_id, 1, f"{asset_id}.jpg", "PRESENT", 123, 1))
        # Two paths for one asset still represent one photo.
        self.connection.execute("INSERT INTO file_locations VALUES (?,?,?,?,?,?,?)",
            (10000, 1, 1, "duplicate-copy.jpg", "PRESENT", 123, 1))
        # Same asset has a location in another source; scope checks stay source-bound.
        self.connection.execute("INSERT INTO file_locations VALUES (?,?,?,?,?,?,?)",
            (10001, 1, 2, "copy.jpg", "PRESENT", 123, 1))
        self.connection.execute("INSERT INTO assets VALUES (652,?,?,?)", ("f" * 64, 12, 652))
        self.connection.execute("INSERT INTO file_locations VALUES (?,?,?,?,?,?,?)",
            (10002, 652, 1, "movie.mp4", "PRESENT", 12, 1))
        self.connection.execute("INSERT INTO assets VALUES (653,?,?,?)", ("e" * 64, 12, 653))
        self.connection.execute("INSERT INTO file_locations VALUES (?,?,?,?,?,?,?)",
            (10003, 653, 1, "offline.jpg", "MISSING", 12, 1))
        self.connection.execute("INSERT INTO assets VALUES (654,?,?,?)", ("d" * 64, 12, 654))
        self.connection.execute("INSERT INTO file_locations VALUES (?,?,?,?,?,?,?)",
            (10004, 654, 2, "other.jpg", "PRESENT", 12, 1))
        self.connection.commit()

    @contextmanager
    def read_snapshot(self):
        self.connection.execute("BEGIN")
        try:
            yield self.connection
        finally:
            self.connection.execute("COMMIT")

    def read_one(self, sql: str, parameters: tuple = ()):
        self.query_log.append(sql)
        return self.connection.execute(sql, parameters).fetchone()

    def read_all(self, sql: str, parameters: tuple = ()):
        self.query_log.append(sql)
        return list(self.connection.execute(sql, parameters))


class EmptyMediaCache:
    def artifact_path(self, *, spec, sha256):
        return Path("Z:/not-a-real-cache") / sha256

    def is_published(self, _path):
        return False


def test_sampling_deduplicates_and_excludes_ineligible_assets_with_bounded_queries() -> None:
    catalog = MiniCatalog()
    result = sample_asset_ids(catalog, source_id=None, count=1000, rng=random.Random(1))
    expected = set(range(1, 651)) | {654}
    assert len(result["asset_ids"]) == len(expected)
    assert set(result["asset_ids"]) == expected
    assert result["candidate_count"] == len(expected)
    assert len(catalog.query_log) <= 4
    assert all("ORDER BY RANDOM" not in sql.upper() for sql in catalog.query_log)

    source_result = sample_asset_ids(catalog, source_id=1, count=1000, rng=random.Random(2))
    assert set(source_result["asset_ids"]) == set(range(1, 651))


def test_random_window_queries_use_primary_and_present_source_indexes() -> None:
    catalog = MiniCatalog()
    global_plan = catalog.connection.execute(
        "EXPLAIN QUERY PLAN SELECT asset_id FROM assets "
        "WHERE asset_id>=? AND asset_id<? ORDER BY asset_id LIMIT ?",
        (1, 1001, 1000),
    ).fetchall()
    source_plan = catalog.connection.execute(
        "EXPLAIN QUERY PLAN SELECT asset_id FROM file_locations "
        "INDEXED BY idx_file_locations_present_source_asset "
        "WHERE source_id=? AND availability='PRESENT' AND asset_id IS NOT NULL "
        "AND asset_id>=? AND asset_id<? GROUP BY asset_id ORDER BY asset_id LIMIT ?",
        (1, 1, 1001, 1000),
    ).fetchall()
    global_details = " ".join(str(row["detail"]) for row in global_plan)
    source_details = " ".join(str(row["detail"]) for row in source_plan)
    assert "INTEGER PRIMARY KEY" in global_details
    assert "idx_file_locations_present_source_asset" in source_details
    assert "SEARCH file_locations" in source_details


def test_circular_random_windows_give_contiguous_endpoints_equal_coverage(monkeypatch) -> None:
    width = 4
    low, high = 1, 31
    coverage = {asset_id: 0 for asset_id in range(low, high + 1)}
    for start in range(low, high + 1):
        for range_start, range_end in random_browse._circular_ranges(start, width, low, high):
            for asset_id in range(range_start, range_end):
                coverage[asset_id] += 1
    assert set(coverage.values()) == {width}

    class SequenceRng:
        def __init__(self):
            self.starts = iter(range(1, 655))

        def randint(self, _low, _high):
            return next(self.starts)

    monkeypatch.setattr(random_browse, "WINDOW_IDS", 4)
    monkeypatch.setattr(random_browse, "WINDOW_COUNT", 654)
    catalog = MiniCatalog()
    sampled = random_browse._random_global_windows(catalog, SequenceRng())
    counts = Counter(sampled)
    assert set(counts) == set(range(1, 651)) | {652, 653, 654}
    assert set(counts.values()) == {4}


def test_recent_exclusion_releases_old_ids_only_when_candidate_pool_is_small() -> None:
    catalog = MiniCatalog()
    first = sample_asset_ids(catalog, source_id=None, count=300, rng=random.Random(3))["asset_ids"]
    second = sample_asset_ids(catalog, source_id=None, count=300, recent_ids=first, rng=random.Random(4))["asset_ids"]
    assert len(first) == len(set(first)) == 300
    assert len(second) == len(set(second)) == 300
    assert set(first).isdisjoint(second)

    only_one = sample_asset_ids(catalog, source_id=None, count=2, recent_ids=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10], rng=random.Random(5))
    assert len(only_one["asset_ids"]) == 2
    assert len(set(only_one["asset_ids"])) == 2


def test_random_summary_rechecks_scope_presence_and_preserves_sample_order() -> None:
    catalog = MiniCatalog()
    cards = random_asset_summaries(catalog, EmptyMediaCache(), [654, 652, 1, 653], source_id=None)
    assert [card["asset_id"] for card in cards] == [654, 1]
    assert cards[1]["location_count"] == 3
    scoped = random_asset_summaries(catalog, EmptyMediaCache(), [1, 654], source_id=1)
    assert [card["asset_id"] for card in scoped] == [1]


def test_random_id_validation_rejects_invalid_and_oversized_payloads() -> None:
    with pytest.raises(ApiError):
        validate_ids([1, True], limit=10)
    with pytest.raises(ApiError):
        validate_ids([1.8], limit=10)
    with pytest.raises(ApiError):
        validate_ids([1, 2], limit=1)
ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "photo_search" / "web"


def test_random_browse_uses_the_shared_photo_page_and_explicit_next_batch() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'id="browse-show-random"' in html
    assert 'id="browse-random-size" type="number" min="1" max="1000"' in html
    assert 'id="browse-random-next">下一批</button>' in html
    assert 'id="browse-random-restart"' not in html
    assert "if (els.browseStatus) els.browseStatus.hidden = useRandom;" in app
    assert 'request("/photo-random-sample"' in app
    assert 'request("/photo-random-summaries"' in app
    assert "random.batchIds.slice(0, 1000)" in app
    assert "random.recentIds.slice(0, 1200)" in app
    assert "els.browseRandomSize.disabled = random.loading || random.nextInFlight || Boolean(random.pendingBatch);" in app
    assert "state.browse.mode !== \"random\" && captureConditionActive()" in app
    assert "random.recentIds = Array.from(new Set(random.batchIds.concat(random.recentIds))).slice(0, 1200)" in app
    activation = app.split("function activateRandomBrowse()", 1)[1].split("function resetRandomForSource()", 1)[0]
    assert "requestNextRandomBatch()" not in activation
    assert activation.index("if (!random.batchIds.length)") < activation.index("if (random.assets.length)")


def test_random_batch_failure_restores_current_batch_and_retries_same_pending_ids() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the random batch recovery check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
const app=fs.readFileSync("src/photo_search/web/app.js","utf8");
function extract(name,next){const start=app.indexOf(`  function ${name}(`);const end=app.indexOf(`\n  function ${next}(`,start);assert.ok(start>=0&&end>start,name);return app.slice(start,end);}
const state={browse:{mode:"random",sourceId:"",loading:false,requestVersion:0,returnScrollY:0,pager:null,
  random:{sourceId:"",batchIds:[1,2],recentIds:[1,2],batchSize:2,loadedPages:1,hasMore:false,loading:false,
    requestVersion:0,nextInFlight:false,pendingBatch:null,assets:[{asset_id:1},{asset_id:2}],skippedCount:0,
    scrollY:0,statusError:"",restorePagesTarget:0}}};
const el=()=>({value:"2",textContent:"",disabled:false,hidden:false,classList:{toggle(){}}});
const els={browseRandomState:el(),browseRandomNext:el(),browseRandomSize:el(),assetGrid:el(),loadMore:el(),gridSentinel:el()};
let failSummary=false,sampleCalls=0;
const request=async(path,{body}={})=>{
  if(path==="/photo-random-sample"){sampleCalls++;return{body:{asset_ids:[3,4],underfilled:false}};}
  if(path==="/photo-random-summaries"){
    if(failSummary){failSummary=false;throw new Error("synthetic summary failure");}
    return{body:{assets:body.asset_ids.map(asset_id=>({asset_id}))}};
  }
  throw new Error(path);
};
const context={state,els,window:{PhotoScrollPager:null,PhotoFavorites:null,scrollTo(){},requestAnimationFrame(fn){fn();}},
  PAGE_SIZE:50,request,Promise,Number,Array,Set,Boolean,Math,String,Date,
  text:(element,value)=>{element.textContent=value;},describe:error=>error.message,persistRandomBrowse(){},
  renderRandomAssets(){const random=state.browse.random;random.hasMore=random.loadedPages*PAGE_SIZE<random.batchIds.length;},
  console};
vm.createContext(context);
vm.runInContext([
  extract("updateRandomStatus","activateRandomBrowse"),
  extract("activateRandomBrowse","resetRandomForSource"),
  extract("readRandomPage","loadRandomPage"),
  extract("loadRandomPage","requestNextRandomBatch"),
  extract("requestNextRandomBatch","timelineRenderAsset"),
].join("\n"),context);
(async()=>{
  const random=state.browse.random;
  random.assets=[];random.pendingBatch={sourceId:"",requestedCount:2,underfilled:false,assetIds:[3,4]};
  await context.activateRandomBrowse();
  assert.equal(sampleCalls,0,"restoring a page must not auto-apply the pending batch");
  assert.equal(JSON.stringify(random.batchIds),"[1,2]");assert.equal(JSON.stringify(random.assets.map(x=>x.asset_id)),"[1,2]");
  random.pendingBatch=null;random.assets=[{asset_id:1},{asset_id:2}];random.loadedPages=1;
  failSummary=true;await context.requestNextRandomBatch();
  assert.equal(JSON.stringify(random.batchIds),"[1,2]","failed next page keeps the old visible batch");
  assert.equal(JSON.stringify(random.assets.map(x=>x.asset_id)),"[1,2]");
  assert.equal(JSON.stringify(random.pendingBatch.assetIds),"[3,4]");
  assert.ok(els.browseRandomState.textContent.includes("synthetic summary failure"));
  assert.equal(els.browseRandomSize.disabled,true);
  await context.requestNextRandomBatch();
  assert.equal(JSON.stringify(random.batchIds),"[3,4]");assert.equal(JSON.stringify(random.assets.map(x=>x.asset_id)),"[3,4]");
  assert.equal(sampleCalls,1,"retry reuses the exact pending IDs instead of sampling again");
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10
    )
    assert result.returncode == 0, result.stderr
