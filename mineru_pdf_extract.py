#!/usr/bin/env python3
"""
使用 MinerU 精准解析 API（需 Token）解析本地或公网 PDF，得到 Markdown 等结果（ZIP）。

本地 PDF：POST /api/v4/file-urls/batch → PUT 上传 → GET /api/v4/extract-results/batch/{batch_id} 轮询。
公网 PDF 直链：POST /api/v4/extract/task → GET /api/v4/extract/task/{task_id} 轮询。

认证：环境变量 MINERU_API_TOKEN（Bearer JWT），勿写入代码或提交仓库。

示例:
  export MINERU_API_TOKEN='你的token'
  ./mineru_pdf_extract.py "./成都七中 李昕熠_P1.pdf" -o ./mineru_out
  ./mineru_pdf_extract.py --url "https://cdn-mineru.openxlab.org.cn/demo/example.pdf" -o ./out
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

import requests

BASE = "https://mineru.net"
BATCH_URLS = f"{BASE}/api/v4/file-urls/batch"
BATCH_RESULTS = f"{BASE}/api/v4/extract-results/batch"
EXTRACT_TASK = f"{BASE}/api/v4/extract/task"
EXTRACT_TASK_ITEM = f"{BASE}/api/v4/extract/task"

# 批量结果中单条 state；done / failed 为终态
BUSY_STATES = frozenset({"waiting-file", "pending", "running", "converting"})


def auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "*/*",
    }


def require_token() -> str:
    t = (os.environ.get("MINERU_API_TOKEN") or "").strip()
    if not t:
        print("请设置环境变量 MINERU_API_TOKEN", file=sys.stderr)
        sys.exit(1)
    return t


def check_api_response(body: dict, context: str) -> dict:
    if body.get("code") != 0:
        msg = body.get("msg", "")
        print(f"{context} 失败: code={body.get('code')} msg={msg}", file=sys.stderr)
        sys.exit(2)
    return body["data"]


def upload_local_and_wait(
    pdf_path: Path,
    token: str,
    model_version: str,
    poll_interval: float,
    timeout: float,
    verbose: bool,
) -> list[dict]:
    name = pdf_path.name
    data_id = str(uuid.uuid4())
    r = requests.post(
        BATCH_URLS,
        headers=auth_headers(token),
        json={
            "files": [{"name": name, "data_id": data_id}],
            "model_version": model_version,
        },
        timeout=120,
    )
    body = r.json()
    data = check_api_response(body, "申请上传链接")
    batch_id = data["batch_id"]
    urls: list[str] = data["file_urls"]
    if len(urls) != 1:
        print(f"意外：返回 {len(urls)} 个上传地址", file=sys.stderr)
        sys.exit(3)

    if verbose:
        print(f"batch_id={batch_id}，正在上传 {pdf_path} …")
    with open(pdf_path, "rb") as f:
        up = requests.put(urls[0], data=f, timeout=600)
    if up.status_code != 200:
        print(f"上传失败 HTTP {up.status_code}: {up.text[:500]}", file=sys.stderr)
        sys.exit(4)
    if verbose:
        print("上传完成，等待解析…")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        gr = requests.get(
            f"{BATCH_RESULTS}/{batch_id}",
            headers=auth_headers(token),
            timeout=60,
        )
        gbody = gr.json()
        if gbody.get("code") != 0:
            print(f"查询批量结果失败: {gbody}", file=sys.stderr)
            time.sleep(poll_interval)
            continue
        results = gbody["data"].get("extract_result") or []
        if not results:
            time.sleep(poll_interval)
            continue
        # 单文件：只看与 data_id / 文件名匹配的一条，或仅一条
        ours = [x for x in results if x.get("data_id") == data_id or x.get("file_name") == name]
        chosen = ours[0] if ours else results[0]
        st = chosen.get("state", "")
        if st in BUSY_STATES:
            if verbose and st == "running":
                prog = chosen.get("extract_progress") or {}
                ep = prog.get("extracted_pages")
                tp = prog.get("total_pages")
                if ep is not None and tp is not None:
                    print(f"  解析进度: {ep}/{tp} 页")
            time.sleep(poll_interval)
            continue
        if st == "failed":
            print(f"解析失败: {chosen.get('err_msg', '')}", file=sys.stderr)
            sys.exit(5)
        if st == "done":
            return results
        time.sleep(poll_interval)

    print("等待结果超时", file=sys.stderr)
    sys.exit(6)
    return []


def extract_by_public_url(
    pdf_url: str,
    token: str,
    model_version: str,
    poll_interval: float,
    timeout: float,
    verbose: bool,
) -> dict:
    r = requests.post(
        EXTRACT_TASK,
        headers=auth_headers(token),
        json={"url": pdf_url, "model_version": model_version},
        timeout=120,
    )
    body = r.json()
    data = check_api_response(body, "创建解析任务")
    task_id = data["task_id"]
    if verbose:
        print(f"task_id={task_id}，轮询中…")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        gr = requests.get(
            f"{EXTRACT_TASK_ITEM}/{task_id}",
            headers=auth_headers(token),
            timeout=60,
        )
        gbody = gr.json()
        if gbody.get("code") != 0:
            time.sleep(poll_interval)
            continue
        d = gbody["data"]
        st = d.get("state", "")
        if st in BUSY_STATES:
            if verbose and st == "running":
                prog = d.get("extract_progress") or {}
                ep = prog.get("extracted_pages")
                tp = prog.get("total_pages")
                if ep is not None and tp is not None:
                    print(f"  解析进度: {ep}/{tp} 页")
            time.sleep(poll_interval)
            continue
        if st == "failed":
            print(f"解析失败: {d.get('err_msg', '')}", file=sys.stderr)
            sys.exit(5)
        if st == "done":
            return d
        time.sleep(poll_interval)

    print("等待结果超时", file=sys.stderr)
    sys.exit(6)
    return {}


def download_zip(url: str, dest: Path, verbose: bool) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if verbose:
        print(f"下载 ZIP → {dest}")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)


def main() -> None:
    p = argparse.ArgumentParser(description="MinerU PDF 解析（精准 API + Token）")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("pdf", nargs="?", type=Path, help="本地 PDF 路径")
    src.add_argument("--url", metavar="URL", help="可公网访问的 PDF 直链（非本地上传）")
    p.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=Path("./mineru_output"),
        help="输出目录（默认 ./mineru_output）",
    )
    p.add_argument(
        "--model",
        default="vlm",
        choices=["vlm", "pipeline", "MinerU-HTML"],
        help="model_version，PDF 建议 vlm（默认）",
    )
    p.add_argument("--poll-interval", type=float, default=3.0, help="轮询间隔秒")
    p.add_argument("--timeout", type=float, default=3600.0, help="最长等待秒")
    p.add_argument("-q", "--quiet", action="store_true", help="减少日志")
    args = p.parse_args()
    verbose = not args.quiet
    token = require_token()

    out_dir = args.out_dir.resolve()

    if args.url:
        if args.model == "MinerU-HTML":
            print("公网 URL 模式用于 PDF 时不应使用 MinerU-HTML", file=sys.stderr)
        data = extract_by_public_url(
            args.url,
            token,
            args.model,
            args.poll_interval,
            args.timeout,
            verbose,
        )
        zip_url = data.get("full_zip_url") or ""
        if not zip_url:
            print("响应中无 full_zip_url", file=sys.stderr)
            sys.exit(7)
        name = Path(args.url.split("?", 1)[0]).name or "result"
        if not name.lower().endswith(".zip"):
            name = "result.zip"
        download_zip(zip_url, out_dir / name, verbose)
        print(str(out_dir / name))
        return

    pdf_path = args.pdf
    if not pdf_path or not pdf_path.is_file():
        print(f"文件不存在: {pdf_path}", file=sys.stderr)
        sys.exit(1)
    if args.model == "MinerU-HTML":
        print("本地 PDF 请使用 vlm 或 pipeline，而非 MinerU-HTML", file=sys.stderr)
        sys.exit(1)

    results = upload_local_and_wait(
        pdf_path.resolve(),
        token,
        args.model,
        args.poll_interval,
        args.timeout,
        verbose,
    )
    # 合并为一条 done 记录（与上传文件名一致）
    name = pdf_path.name
    done = next(
        (x for x in results if x.get("state") == "done" and x.get("file_name") == name),
        next((x for x in results if x.get("state") == "done"), None),
    )
    if not done:
        print(f"未找到完成项: {results}", file=sys.stderr)
        sys.exit(7)
    zip_url = done.get("full_zip_url") or ""
    if not zip_url:
        print("完成记录中无 full_zip_url", file=sys.stderr)
        sys.exit(7)
    zip_name = Path(name).stem + "_mineru.zip"
    download_zip(zip_url, out_dir / zip_name, verbose)
    print(str(out_dir / zip_name))


if __name__ == "__main__":
    main()
