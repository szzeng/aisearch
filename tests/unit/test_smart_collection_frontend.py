from __future__ import annotations

import shutil
import subprocess


def test_smart_collection_reports_refresh_failure_even_with_current_cache() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the smart collection status check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
const document={readyState:"loading",addEventListener(){}},window={};
let code=fs.readFileSync("src/photo_search/web/app.js","utf8");
code=code.replace('  if (document.readyState === "loading")',
  '  globalThis.ui={photoSmartRefreshStatus};\n  if (document.readyState === "loading")');
const context={document,window};vm.createContext(context);vm.runInContext(code,context);
const current={freshness:"CURRENT",execution_kind:"COMBINED",last_error_code:"PHOTO_SMART_REFRESH_FAILED"};
const body={coverage:"INDEXING",assets:[{asset_id:7}]};
const message=context.ui.photoSmartRefreshStatus(current,body);
assert.match(message,/上次刷新失败（PHOTO_SMART_REFRESH_FAILED），可重试。/);
assert.match(message,/索引处理中/);
assert.equal(context.ui.photoSmartRefreshStatus({...current,last_error_code:null},body),
  "索引处理中；已发布候选仍可浏览并标记为待更新。");
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
