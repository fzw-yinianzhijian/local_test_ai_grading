#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


@dataclass
class Config:
    ocr_token: str
    llm_api_key: str
    llm_base_url: str
    llm_model: str


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")



def load_dotenv(env_path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not env_path.exists():
        return data

    for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        k, v = line.split("=", 1)
        key = k.strip()
        val = v.strip()

        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]

        data[key] = val

    return data



def build_config(repo_root: Path) -> Config:
    env_file = load_dotenv(repo_root / ".env")

    def get_env(name: str) -> str:
        val = (os.getenv(name) or env_file.get(name) or "").strip()
        if not val:
            raise RuntimeError(f"missing required env: {name}")
        return val

    return Config(
        ocr_token=get_env("TOKEN_OF_OCR"),
        llm_api_key=get_env("API_OF_QWEN"),
        llm_base_url=get_env("BASEURL"),
        llm_model=get_env("MODELNAME"),
    )



def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)



def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))



def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()



def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()



def normalize_name(name: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|]", "_", name).strip()
    return s or "unknown"



def pick_answer_pdf(answer_dir: Path) -> Path:
    cands = sorted(p for p in answer_dir.glob("*.pdf") if p.is_file())
    if not cands:
        raise RuntimeError(f"no answer pdf in {answer_dir}")
    return cands[0]



def list_student_pdfs(student_dir: Path) -> list[Path]:
    return sorted(p for p in student_dir.glob("*.pdf") if p.is_file())


class RunLogger:
    def __init__(self, events_path: Path, verbose: bool = False) -> None:
        self.events_path = events_path
        self.verbose = verbose
        self.events_path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, **kwargs: Any) -> None:
        row = {"ts": now_iso(), **kwargs}
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if self.verbose:
            print(json.dumps(row, ensure_ascii=False))



def run_mineru_ocr(repo_root: Path, pdf_path: Path, out_dir: Path, cfg: Config, logger: RunLogger) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(repo_root / "mineru_pdf_extract.py"),
        str(pdf_path),
        "-o",
        str(out_dir),
        "--model",
        "vlm",
        "--poll-interval",
        "3",
        "--timeout",
        "3600",
    ]

    env = os.environ.copy()
    env["MINERU_API_TOKEN"] = cfg.ocr_token

    max_attempts = 4
    p: subprocess.CompletedProcess[str] | None = None
    for i in range(1, max_attempts + 1):
        logger.event(stage="ocr", action="run", file=str(pdf_path), attempt=i, command=" ".join(cmd))
        p = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if p.returncode == 0:
            break
        if i < max_attempts:
            delay = min(2**i + random.random(), 20)
            logger.event(
                stage="ocr",
                action="retry",
                file=str(pdf_path),
                attempt=i,
                rc=p.returncode,
                delay=delay,
                stderr=(p.stderr or "")[:400],
            )
            time.sleep(delay)
        else:
            logger.event(stage="ocr", action="error", file=str(pdf_path), rc=p.returncode, stderr=(p.stderr or "")[:1000])
            raise RuntimeError(f"mineru failed for {pdf_path.name}: rc={p.returncode}")

    assert p is not None
    lines = [x.strip() for x in (p.stdout or "").splitlines() if x.strip()]
    zip_path: Path | None = None
    for line in reversed(lines):
        if line.endswith(".zip"):
            cand = Path(line)
            if cand.exists():
                zip_path = cand
                break
    if zip_path is None:
        zips = sorted(out_dir.glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True)
        if zips:
            zip_path = zips[0]

    if zip_path is None or not zip_path.exists():
        raise RuntimeError(f"cannot locate mineru zip output for {pdf_path}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        md_name = next((n for n in zf.namelist() if n.endswith("full.md")), None)
        if not md_name:
            raise RuntimeError(f"full.md not found in zip: {zip_path}")
        content = zf.read(md_name).decode("utf-8", errors="ignore")

    md_path = out_dir / "full.md"
    atomic_write_text(md_path, content)
    atomic_write_json(
        out_dir / "ocr_meta.json",
        {
            "source_pdf": str(pdf_path),
            "source_pdf_sha256": sha256_file(pdf_path),
            "zip": str(zip_path),
            "md_path": str(md_path),
            "generated_at": now_iso(),
        },
    )
    logger.event(stage="ocr", action="done", file=str(pdf_path), md=str(md_path))
    return md_path



def to_message_text(x: Any) -> str:
    if isinstance(x, str):
        return x
    if isinstance(x, list):
        parts: list[str] = []
        for item in x:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if x is None:
        return ""
    return str(x)



def call_llm(prompt: str, cfg: Config, logger: RunLogger, stage: str, max_tokens: int = 3600) -> tuple[str, dict[str, Any]]:
    base = cfg.llm_base_url.rstrip("/")
    url = f"{base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg.llm_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": cfg.llm_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }

    max_attempts = 5
    for i in range(1, max_attempts + 1):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=300)
        except requests.RequestException as exc:
            if i >= max_attempts:
                raise RuntimeError(f"llm network error: {exc}") from exc
            delay = min(2 ** i + random.random(), 20)
            logger.event(stage=stage, action="retry", attempt=i, reason=f"network:{exc}", delay=delay)
            time.sleep(delay)
            continue

        if resp.status_code in {429, 500, 502, 503, 504} and i < max_attempts:
            delay = min(2 ** i + random.random(), 20)
            logger.event(stage=stage, action="retry", attempt=i, reason=f"http:{resp.status_code}", delay=delay)
            time.sleep(delay)
            continue

        if resp.status_code != 200:
            raise RuntimeError(f"llm http={resp.status_code}, body={resp.text[:500]}")

        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = to_message_text(msg.get("content"))
        if not text:
            raise RuntimeError("llm returned empty content")
        return text, data

    raise RuntimeError("llm max retries exceeded")



def build_judge_prompt(answer_md: str, student_md: str, exam_name: str, student_name: str) -> str:
    return f"""你是数学考试阅卷助手。请严格按照下面步骤执行并输出 Markdown。

考试：{exam_name}
考生：{student_name}

【第一个文件：题目与标答】
```markdown
{answer_md}
```

【第二个文件：作答md】
```markdown
{student_md}
```

请执行以下步骤：
1. 阅读题目与标答，记住分步骤给分点。
2. 对齐题号与作答，确保每个作答对应正确题目。
3. 识别题型（选择/多选/填空/解答），客观题按答案正误评分；多选需明确“全对满分/部分给分”规则。
4. 对每道解答题，先对每一行（或几行合成一句）给一句话内容说明。
5. 给出整题“作答思路梳理”，明确指出跳步、错误、遗漏、表述不清，并给出给分/扣分意见。
6. 结合“行级说明 + 思路梳理 + 标答 + 给分点”给出每题得分。
7. 逐条列出扣分项与扣分分值；对于不完美但不扣分的步骤，给出不扣分理由。
8. 输出整张卷子打分表（每题满分、每题实得分）。
9. 汇总总分。

输出要求补充：
- 保持结论清晰但避免冗长重复。
- 每道题最多保留必要说明，不要生成无关长段落。

输出格式（必须包含以下一级标题）：
# 最终评改结果
## 最终作答总分
## 逐题得分表
## 解答题行级说明
## 作答思路梳理
## 扣分项说明
"""



def build_check_prompt(
    answer_md: str,
    student_md: str,
    judge_md: str,
    exam_name: str,
    student_name: str,
    student_pdf_name: str,
) -> str:
    return f"""你是评阅复核助手。请严格按要求输出 Markdown。

考试：{exam_name}
考生：{student_name}
原试卷文件名：{student_pdf_name}

【第一个文件：卷子pdf（说明）】
注意：当前通过本地 API 流程处理，提供的是该卷子的 OCR 提取视图与文件名用于复核，不要凭空造错。

【第二个文件：作答md】
```markdown
{student_md}
```

【第三个文件：题目与标答】
```markdown
{answer_md}
```

【第四个文件：评改结果】
```markdown
{judge_md}
```

任务：检查评改结果是否真实反映作答情况。
- 第一步：检查是否存在“作答提取错误/提取模糊/自动修正”三类情况；仅在证据充分时指出，不得强行挑错。
- 第二步：基于检查结果，指出作答思路梳理和评改结果中需要修改或重新打分的地方。
- 第三步：输出一段综合检查说明。
- 第四步：给出“检查后评改结果”。

输出要求补充：
- 检查结论要有证据，避免空泛重复。
- 仅输出必要修正，文本尽量精炼。

输出格式（必须包含以下一级标题）：
# 作答检查结果md
# 检查说明
# 检查后评改结果.md
"""



def parse_check_sections(text: str) -> tuple[str, str]:
    check_part = text.strip()
    final_part = text.strip()

    pat = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
    matches = list(pat.finditer(text))
    if not matches:
        return check_part, final_part

    sections: dict[str, str] = {}
    for idx, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        sections[title] = text[start:end].strip()

    for k, v in sections.items():
        if "作答检查结果" in k:
            check_part = f"# {k}\n\n{v}".strip()
        if "检查后评改结果" in k:
            final_part = f"# {k}\n\n{v}".strip()

    def _tiny(s: str) -> bool:
        pure = re.sub(r"\s+", "", s or "")
        return len(pure) < 30

    if _tiny(check_part):
        check_part = text.strip()
    if _tiny(final_part):
        # fallback: if extracted final section is empty, keep full check text
        final_part = text.strip()

    return check_part, final_part



def load_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"stages": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"stages": {}}



def stage_cache_hit(status: dict[str, Any], stage: str, input_hash: str) -> bool:
    s = (status.get("stages") or {}).get(stage) or {}
    return bool(s.get("ok") and s.get("input_hash") == input_hash)



def mark_stage(status: dict[str, Any], stage: str, ok: bool, input_hash: str, info: dict[str, Any]) -> dict[str, Any]:
    status.setdefault("stages", {})[stage] = {
        "ok": ok,
        "input_hash": input_hash,
        "updated_at": now_iso(),
        **info,
    }
    status["updated_at"] = now_iso()
    return status



def process_student(
    repo_root: Path,
    exam_dir: Path,
    answer_md_text: str,
    answer_pdf: Path,
    student_pdf: Path,
    run_logger: RunLogger,
    cfg: Config,
    force: bool,
) -> dict[str, Any]:
    exam_name = exam_dir.name
    student_name = student_pdf.stem

    eval_dir = exam_dir / "eval" / normalize_name(student_name)
    eval_dir.mkdir(parents=True, exist_ok=True)
    status_path = eval_dir / "status.json"
    status = load_status(status_path)

    # stage 1: OCR for student (answer OCR handled once per exam)
    student_ocr_cache = exam_dir / "eval" / "_cache" / "ocr" / sha256_file(student_pdf)
    student_md_path = student_ocr_cache / "full.md"

    ocr_hash = sha256_text(sha256_file(student_pdf))
    if force or not stage_cache_hit(status, "ocr", ocr_hash) or not student_md_path.exists():
        try:
            student_md_path = run_mineru_ocr(repo_root, student_pdf, student_ocr_cache, cfg, run_logger)
            status = mark_stage(status, "ocr", True, ocr_hash, {"md_path": str(student_md_path)})
            run_logger.event(exam=exam_name, student=student_name, stage="ocr", result="ok")
        except Exception as exc:
            status = mark_stage(status, "ocr", False, ocr_hash, {"error": str(exc)})
            atomic_write_json(status_path, status)
            run_logger.event(exam=exam_name, student=student_name, stage="ocr", result="failed", error=str(exc))
            return {"student": student_name, "ok": False, "failed_stage": "ocr", "error": str(exc)}
    else:
        run_logger.event(exam=exam_name, student=student_name, stage="ocr", result="cache-hit")

    student_md_text = student_md_path.read_text(encoding="utf-8", errors="ignore")
    atomic_write_text(eval_dir / "题目与标答.md", answer_md_text)
    atomic_write_text(eval_dir / "作答md.md", student_md_text)

    # stage 2: judge
    judge_input_hash = sha256_text(
        "\n".join([sha256_text(answer_md_text), sha256_text(student_md_text), cfg.llm_model, "judge_prompt_v1"])
    )
    judge_out_path = eval_dir / "评改结果.md"
    judge_raw_path = eval_dir / "judge_raw.json"

    if force or not stage_cache_hit(status, "judge", judge_input_hash) or not judge_out_path.exists():
        try:
            prompt = build_judge_prompt(answer_md_text, student_md_text, exam_name, student_name)
            judge_text, judge_raw = call_llm(prompt, cfg, run_logger, stage="judge", max_tokens=3200)
            atomic_write_text(judge_out_path, judge_text)
            atomic_write_json(judge_raw_path, judge_raw)
            status = mark_stage(status, "judge", True, judge_input_hash, {"output": str(judge_out_path)})
            run_logger.event(exam=exam_name, student=student_name, stage="judge", result="ok")
        except Exception as exc:
            status = mark_stage(status, "judge", False, judge_input_hash, {"error": str(exc)})
            atomic_write_json(status_path, status)
            run_logger.event(exam=exam_name, student=student_name, stage="judge", result="failed", error=str(exc))
            return {"student": student_name, "ok": False, "failed_stage": "judge", "error": str(exc)}
    else:
        run_logger.event(exam=exam_name, student=student_name, stage="judge", result="cache-hit")

    judge_text = judge_out_path.read_text(encoding="utf-8", errors="ignore")

    # stage 3: check
    check_input_hash = sha256_text(
        "\n".join([sha256_text(answer_md_text), sha256_text(student_md_text), sha256_text(judge_text), cfg.llm_model, "check_prompt_v1"])
    )
    check_out_path = eval_dir / "作答检查结果.md"
    final_out_path = eval_dir / "检查后评改结果.md"
    check_raw_path = eval_dir / "check_raw.json"

    if force or not stage_cache_hit(status, "check", check_input_hash) or not final_out_path.exists():
        try:
            prompt = build_check_prompt(
                answer_md=answer_md_text,
                student_md=student_md_text,
                judge_md=judge_text,
                exam_name=exam_name,
                student_name=student_name,
                student_pdf_name=student_pdf.name,
            )
            check_text, check_raw = call_llm(prompt, cfg, run_logger, stage="check", max_tokens=3600)
            check_part, final_part = parse_check_sections(check_text)
            atomic_write_text(check_out_path, check_part)
            atomic_write_text(final_out_path, final_part)
            atomic_write_json(check_raw_path, check_raw)
            status = mark_stage(status, "check", True, check_input_hash, {"output": str(final_out_path)})
            run_logger.event(exam=exam_name, student=student_name, stage="check", result="ok")
        except Exception as exc:
            status = mark_stage(status, "check", False, check_input_hash, {"error": str(exc)})
            atomic_write_json(status_path, status)
            run_logger.event(exam=exam_name, student=student_name, stage="check", result="failed", error=str(exc))
            return {"student": student_name, "ok": False, "failed_stage": "check", "error": str(exc)}
    else:
        run_logger.event(exam=exam_name, student=student_name, stage="check", result="cache-hit")

    atomic_write_json(status_path, status)
    return {"student": student_name, "ok": True}



def process_exam(
    repo_root: Path,
    exam_dir: Path,
    cfg: Config,
    run_logger: RunLogger,
    force: bool,
    max_students: int | None,
    student_regex: str | None,
    sleep_sec: float,
) -> dict[str, Any]:
    exam_name = exam_dir.name
    answer_pdf = pick_answer_pdf(exam_dir / "answer")
    students = list_student_pdfs(exam_dir / "student")
    if student_regex:
        rx = re.compile(student_regex)
        students = [s for s in students if rx.search(s.name)]
    if max_students is not None:
        students = students[:max_students]

    run_logger.event(exam=exam_name, stage="exam", action="start", student_count=len(students), answer=answer_pdf.name)

    answer_ocr_cache = exam_dir / "eval" / "_cache" / "ocr" / sha256_file(answer_pdf)
    answer_md_path = answer_ocr_cache / "full.md"
    if force or not answer_md_path.exists():
        answer_md_path = run_mineru_ocr(repo_root, answer_pdf, answer_ocr_cache, cfg, run_logger)
    else:
        run_logger.event(exam=exam_name, stage="ocr", action="cache-hit", file=answer_pdf.name)
    answer_md_text = answer_md_path.read_text(encoding="utf-8", errors="ignore")

    summary = {"exam": exam_name, "total": len(students), "ok": 0, "failed": 0, "failed_items": []}

    for idx, student_pdf in enumerate(students, start=1):
        run_logger.event(exam=exam_name, stage="student", action="start", index=idx, student=student_pdf.name)
        result = process_student(repo_root, exam_dir, answer_md_text, answer_pdf, student_pdf, run_logger, cfg, force)
        if result.get("ok"):
            summary["ok"] += 1
        else:
            summary["failed"] += 1
            summary["failed_items"].append(result)
        if sleep_sec > 0 and idx < len(students):
            time.sleep(sleep_sec)

    run_logger.event(exam=exam_name, stage="exam", action="done", ok=summary["ok"], failed=summary["failed"])
    return summary



def main() -> None:
    parser = argparse.ArgumentParser(description="Batch OCR->judge->check for ai_grading_manul")
    parser.add_argument("--repo-root", default=".", help="repo root path")
    parser.add_argument("--data-dir", default="data", help="data directory relative to repo root")
    parser.add_argument("--exam", nargs="*", default=None, help="exam folders, e.g. test1 test2")
    parser.add_argument("--max-students", type=int, default=None, help="limit students per exam")
    parser.add_argument("--student-regex", default=None, help="filter student filename by regex")
    parser.add_argument("--force", action="store_true", help="force rerun all stages")
    parser.add_argument("--sleep", type=float, default=0.5, help="sleep seconds between students")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    data_dir = (repo_root / args.data_dir).resolve()
    cfg = build_config(repo_root)

    if not data_dir.exists():
        raise RuntimeError(f"data dir not found: {data_dir}")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = repo_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    exams = args.exam if args.exam else [p.name for p in sorted(data_dir.iterdir()) if p.is_dir()]
    llm_endpoint = f"{cfg.llm_base_url.rstrip('/')}/chat/completions"

    overall = {
        "run_id": run_id,
        "started_at": now_iso(),
        "repo_root": str(repo_root),
        "exams": exams,
        "model": cfg.llm_model,
        "llm_endpoint": llm_endpoint,
        "results": [],
    }

    print(f"[run] model={cfg.llm_model}")
    print(f"[run] llm_endpoint={llm_endpoint}")

    for exam_name in exams:
        exam_dir = data_dir / exam_name
        if not exam_dir.is_dir():
            print(f"[WARN] exam dir missing: {exam_dir}")
            continue

        run_dir = exam_dir / "eval" / "_runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            run_dir / "run_meta.json",
            {
                "run_id": run_id,
                "exam": exam_name,
                "started_at": now_iso(),
                "force": args.force,
                "max_students": args.max_students,
                "student_regex": args.student_regex,
                "model": cfg.llm_model,
            },
        )

        run_logger = RunLogger(run_dir / "events.jsonl", verbose=args.verbose)
        summary = process_exam(
            repo_root=repo_root,
            exam_dir=exam_dir,
            cfg=cfg,
            run_logger=run_logger,
            force=args.force,
            max_students=args.max_students,
            student_regex=args.student_regex,
            sleep_sec=args.sleep,
        )
        overall["results"].append(summary)

    overall["finished_at"] = now_iso()
    atomic_write_json(report_dir / f"run_{run_id}.json", overall)

    lines = [
        f"# Batch Run Report ({run_id})",
        "",
        f"- started_at: {overall['started_at']}",
        f"- finished_at: {overall['finished_at']}",
        f"- model: {overall['model']}",
        "",
        "## Summary",
        "",
        "| exam | total | ok | failed |",
        "|---|---:|---:|---:|",
    ]
    for item in overall["results"]:
        lines.append(f"| {item['exam']} | {item['total']} | {item['ok']} | {item['failed']} |")

    lines.append("\n## Failed Items\n")
    has_failed = False
    for item in overall["results"]:
        for f in item.get("failed_items", []):
            has_failed = True
            lines.append(f"- {item['exam']} / {f.get('student')} / {f.get('failed_stage')} / {f.get('error')}")
    if not has_failed:
        lines.append("- none")

    report_md = "\n".join(lines) + "\n"
    atomic_write_text(report_dir / f"run_{run_id}.md", report_md)

    print(report_md)


if __name__ == "__main__":
    main()
