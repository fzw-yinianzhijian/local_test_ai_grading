#!/usr/bin/env python3
"""
解析同目录下的 MinerU API 文档快照 description.html：
提取 article 中的标题层级、段落、表格、代码块与 JSON-LD 摘要。

Token 请通过环境变量提供，勿写入代码或提交到版本库：
  export MINERU_API_TOKEN='你的 JWT'

用法:
  python parse_description.py                    # 打印结构化摘要到标准输出
  python parse_description.py --json doc.json   # 写入 JSON
  python parse_description.py --jwt-info          # 仅解析 JWT payload（需 MINERU_API_TOKEN）
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag


def _script_dir() -> Path:
    return Path(__file__).resolve().parent


def decode_jwt_payload_unverified(token: str) -> dict[str, Any]:
    """仅 Base64URL 解码 JWT 中段 payload，不校验签名（用于本地查看声明）。"""
    parts = token.strip().split(".")
    if len(parts) < 2:
        raise ValueError("不是有效的 JWT（至少需要 header.payload）")
    payload_b64 = parts[1]
    pad = (-len(payload_b64)) % 4
    payload_b64 += "=" * pad
    raw = base64.urlsafe_b64decode(payload_b64)
    return json.loads(raw.decode("utf-8"))


def _cell_text(cell: Tag) -> str:
    return " ".join(cell.stripped_strings)


def parse_table(table: Tag) -> dict[str, Any]:
    thead = table.find("thead")
    tbody = table.find("tbody") or table
    headers: list[str] = []
    if thead:
        row = thead.find("tr")
        if row:
            headers = [_cell_text(th) for th in row.find_all(["th", "td"])]
    rows: list[list[str]] = []
    for tr in tbody.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        if thead and tr.parent == thead:
            continue
        rows.append([_cell_text(c) for c in cells])
    return {"headers": headers, "rows": rows}


def _code_block_pre(pre: Tag) -> dict[str, str]:
    code = pre.find("code")
    lang = ""
    if code and code.get("class"):
        for c in code["class"]:
            if isinstance(c, str) and c.startswith("language-"):
                lang = c.replace("language-", "", 1)
                break
    text = pre.get_text(separator="\n", strip=False)
    return {"language": lang, "source": text.strip()}


def parse_article(html_path: Path) -> dict[str, Any]:
    raw = html_path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw, "html.parser")

    article = soup.find("article", class_=lambda c: c and "prose" in str(c).split())
    if not article:
        raise SystemExit(f"未找到 article.prose：{html_path}")

    sections: list[dict[str, Any]] = []
    json_ld: list[dict[str, Any]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        if script.string:
            try:
                json_ld.append(json.loads(script.string))
            except json.JSONDecodeError:
                pass

    for el in article.descendants:
        if not isinstance(el, Tag):
            continue
        name = el.name
        if name == "h1":
            sections.append({"type": "heading", "level": 1, "id": el.get("id", ""), "text": el.get_text(strip=True)})
        elif name == "h2":
            sections.append({"type": "heading", "level": 2, "id": el.get("id", ""), "text": el.get_text(strip=True)})
        elif name == "h3":
            sections.append({"type": "heading", "level": 3, "id": el.get("id", ""), "text": el.get_text(strip=True)})
        elif name == "p" and el.parent == article:
            t = el.get_text(" ", strip=True)
            if t:
                sections.append({"type": "paragraph", "text": t})
        elif name == "ul" and el.parent == article:
            items = [li.get_text(" ", strip=True) for li in el.find_all("li", recursive=False)]
            if items:
                sections.append({"type": "list", "ordered": False, "items": items})
        elif name == "ol" and el.parent == article:
            items = [li.get_text(" ", strip=True) for li in el.find_all("li", recursive=False)]
            if items:
                sections.append({"type": "list", "ordered": True, "items": items})
        elif name == "blockquote" and el.parent == article:
            inner = " ".join(el.stripped_strings)
            if inner:
                sections.append({"type": "blockquote", "text": inner})

    # 单独收集表格与 pre（避免与上面重复遍历遗漏）
    for table in article.find_all("table"):
        sections.append({"type": "table", **parse_table(table)})
    for pre in article.find_all("pre"):
        blk = _code_block_pre(pre)
        if blk["source"]:
            sections.append({"type": "code", **blk})

    # 从正文中抽取 MinerU API 端点（便于快速浏览）
    api_urls = sorted(
        set(
            m.group(0)
            for s in sections
            if s.get("type") == "code" and "source" in s
            for m in re.finditer(r"https://mineru\.net/api/[^\s\'\"`]+", s["source"])
        )
    )

    return {
        "source_file": str(html_path),
        "title": soup.title.string.strip() if soup.title and soup.title.string else "",
        "json_ld_count": len(json_ld),
        "json_ld_headlines": [
            x.get("headline") or x.get("name")
            for x in json_ld
            if isinstance(x, dict) and (x.get("headline") or x.get("name"))
        ],
        "section_count": len(sections),
        "sections": sections,
        "endpoints_found_in_code": api_urls,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="解析 description.html（MinerU API 文档快照）")
    parser.add_argument(
        "html_file",
        nargs="?",
        default=str(_script_dir() / "description.html"),
        help="HTML 路径，默认：脚本同目录 description.html",
    )
    parser.add_argument("--json", dest="json_out", metavar="PATH", help="将完整解析结果写入 JSON 文件")
    parser.add_argument("--jwt-info", action="store_true", help="打印环境变量 MINERU_API_TOKEN 的 JWT payload（不校验签名）")
    args = parser.parse_args()

    if args.jwt_info:
        tok = os.environ.get("MINERU_API_TOKEN", "").strip()
        if not tok:
            print("请设置环境变量 MINERU_API_TOKEN", file=sys.stderr)
            sys.exit(1)
        try:
            payload = decode_jwt_payload_unverified(tok)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"JWT 解析失败: {e}", file=sys.stderr)
            sys.exit(1)
        return

    path = Path(args.html_file)
    if not path.is_file():
        print(f"文件不存在: {path}", file=sys.stderr)
        sys.exit(1)

    result = parse_article(path)

    if args.json_out:
        out = Path(args.json_out)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {out}")

    # 控制台摘要：不刷屏，只列标题与端点
    print(f"来源: {result['source_file']}")
    print(f"页面标题: {result['title']}")
    print(f"段落/标题/表格/代码块等区块数: {result['section_count']}")
    if result["endpoints_found_in_code"]:
        print("代码块中出现的 API 地址:")
        for u in result["endpoints_found_in_code"]:
            print(f"  - {u}")
    print("\n文档标题结构（h1–h3）:")
    for s in result["sections"]:
        if s.get("type") != "heading":
            continue
        indent = "  " * (s["level"] - 1)
        hid = s.get("id") or ""
        print(f"{indent}[h{s['level']}] {s['text']}  #{hid}".rstrip())

    if not args.json_out:
        print("\n（使用 --json out.json 可导出含表格与代码全文）", file=sys.stderr)


if __name__ == "__main__":
    main()
