# Photo Search

在本机检索个人照片：中文描述、以图搜图、图片文字，以及人物、地点和拍摄时间筛选。原图只读，索引和模型保存在独立应用数据目录。

支持 Windows + NVIDIA 独显，以及 Apple Silicon macOS。图文采用 FG-CLIP 2 Base 768D，OCR 采用 RapidOCR 3.9.2 / PP-OCRv6 small；SQLite 管理目录与文字索引，LanceDB 管理图片向量。

## 安装和运行

使用 Python 3.12.10 和 uv。源码运行需先用 Node.js/npm 构建界面（Vue 3 + TypeScript + Naive UI）：

```sh
npm --prefix frontend ci --cache .npm-cache
npm --prefix frontend run build
```

构建后由 FastAPI 提供所有页面和资源，日常运行无需 Node 服务或外网。修改界面时运行 `npm --prefix frontend run dev`，构建会自动更新，刷新应用页面即可。发布 Python wheel 或安装包前也需要先构建界面。

Windows PowerShell：

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$PWD\.python"
uv sync --all-groups
uv run python tools/prepare_models/prepare_models.py --data-root D:\PhotoSearchData
uv run photo-search --data-root D:\PhotoSearchData
```

Apple Silicon macOS 终端：

```sh
export UV_CACHE_DIR="$PWD/.uv-cache"
export UV_PYTHON_INSTALL_DIR="$PWD/.python"
uv sync --all-groups
uv run python tools/prepare_models/prepare_models.py
uv run photo-search
```

macOS 默认数据目录为 `~/Library/Application Support/PhotoSearchData`。图文自动使用 Apple GPU（MPS），OCR/人脸使用 CPU。Windows 默认 CUDA。可用 `--model-device mps`、`cuda` 或 `cpu` 明确选择；所选 GPU 不可用时会报告错误，不悄悄切换 CPU。

模型准备命令会下载约 1.8GB 文件，包括固定版本的模型代码、图文权重、OCR 和人脸权重；完成后搜索不需要联网。数据目录必须位于安装/源码目录外。可用 `--models embedding`、`ocr` 或 `face` 只安装指定部分。

启动后按终端给出的本机地址打开界面，添加照片目录并建立索引。首次建库期间可以查看进度，停止程序后可重新继续。更换图文模型或预处理后必须重新建库；旧模型向量不能供新模型查询。

地点页按国家、省、市、区县逐级浏览，景区可跨行政区重叠。GPS 在本机按已安装边界包归类，人工整理优先。边界包可在“地点 → 离线地点数据”导入；准备工具及来源见 [地点任务记录](docs/tasks/place-geography.md)。当前公开中国包属于历史区划，景区包仅接入西湖，覆盖和年份会显示在界面。

当前改动、验证范围和未完成项见 [开发状态](docs/development-status.md)。最低显卡目标为 GTX 1660 6GB；当前版本的新模型尚未完成该设备的实际验收。

## 从旧版本升级

1. 停止应用，先备份原应用数据目录；保留原图和旧模型文件。使用内置备份时运行 `uv run photo-search backup create --data-root <数据目录> --bundle <新的备份目录> --stopped`。
2. 按上面的安装步骤同步 Python 依赖、构建前端并准备新模型。首次打开旧目录会先备份 SQLite，再迁移到 schema 18；人物、人工地点、别名、撤销历史和资产 ID 保留。
3. 在“图库管理”对原来源执行“开始建库”。旧 Chinese-CLIP 向量不能用于 FG-CLIP 2 查询；新模型会使用独立空间重建图文索引。OCR 处理器也已升级，需要补齐新 OCR 产物。重建无需重做人脸模型或清空人工整理。
4. 地理边界包独立导入，换包只重算已有 GPS 的地点归属，不重新编码图片。GPS 缺失时仍可手动整理；行政区划年份和景区覆盖以实际数据包为准。

升级后的目录不能交给只支持旧 schema 的程序直接使用。回退应在停机后将备份恢复到新的数据目录，再用对应旧版本打开；不要删库来解决模型空间不匹配。

## 公开数据集测试

以下命令下载 COCO-CN 和 Flickr8k 的测试集（各 1,000 张），然后用当前模型测试。数据、索引和详细成绩留在应用数据目录的 `benchmarks/`，不扫描其他照片目录。

```sh
uv run python tools/evaluation/download_benchmarks.py
uv run python tools/evaluation/retrieval_benchmark.py --device auto
```

标准检索成绩按每个数据集自己的候选池计算；应用 API 的抽样成绩另列。千图测试不代表十万图吞吐，公开数据也不保证未被模型训练见过。
