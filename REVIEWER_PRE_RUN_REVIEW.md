A. 结论摘要
- 本轮新增（`WORKFLOW.md`、`scripts/run_batch_eval.py`、`.gitignore`）整体方向正确，主流程已经具备“按 exam 批量执行 OCR->judge->check 并写入 `data/*/eval`”的可执行骨架。
- 但在“全量预跑前”仍有 2 个关键风险建议先修（见 D），否则大批量运行时稳定性与留痕完整性不足。

B. 可执行性核验（已实际检查）
- 语法检查通过：`python3 -m py_compile scripts/run_batch_eval.py`。
- CLI 入口可用：`python3 scripts/run_batch_eval.py --help` 正常输出参数。
- 零外部调用 dry-run 可运行：`--exam __no_such_exam__` 能完成并生成 run report（说明主流程、报告落盘链路通）。
- 文档与脚本目标一致：
  - `WORKFLOW.md` 声明输出到 `data/<exam>/eval/...`；
  - `run_batch_eval.py` 实际写入 `eval/<student>`、`eval/_runs/<run_id>` 与 `reports/run_<run_id>.md/json`。

C. 已具备能力（正向）
- 分阶段状态机与缓存：`status.json` + `input_hash`（ocr/judge/check）可支持断点续跑与 cache-hit。
- 失败隔离：单学生失败不会中断整批（`failed_items` 汇总）。
- LLM 调用有重试：对网络异常与 `429/5xx` 有指数退避重试。
- 结果留痕：每个 exam 有 `run_meta.json` + `events.jsonl`，全局有 `reports/run_*.md/json`。

D. 主要风险与建议（按优先级）
- P1（建议预跑前修复）OCR 阶段缺少显式重试：
  - 位置：`run_batch_eval.py` 中 `run_mineru_ocr(...)` 只执行一次子进程调用。
  - 影响：OCR 服务短暂抖动时，学生会直接失败并进入失败清单，批量稳定性下降。
  - 建议：对 OCR 阶段增加与 LLM 同级别的重试包装（可重试：网络错误/429/5xx/超时），并写入 retry 事件。
- P1（建议预跑前确认）留痕文件被 `.gitignore` 屏蔽：
  - 当前 `.gitignore` 忽略 `data/` 与 `reports/`。
  - 影响：运行证据默认不会进入 Git 历史，不利于“全程留痕可审计（跨人复核）”。
  - 建议：至少保留可提交的摘要证据路径（例如 `run_logs/*.md` 不忽略），或在 WORKFLOW 明确“留痕仅本地，不入库”。
- P2 `.env` 解析器较弱：
  - 位置：`load_dotenv` 仅按 `k=v` 裸切分，不处理 `export KEY=...`、引号包裹值。
  - 影响：某些 `.env` 写法会导致鉴权值读取异常。
  - 建议：改用 `python-dotenv` 或补齐解析兼容逻辑。
- P2 `BASEURL` 路径约定未文档化：
  - 位置：LLM URL 固定拼接 `BASEURL.rstrip('/') + '/chat/completions'`。
  - 影响：若供应商要求 `/v1/chat/completions`，需依赖 `.env` 手工拼成含 `/v1` 的 BASEURL，易误配。
  - 建议：在 WORKFLOW 增加示例（例如 `BASEURL=https://xxx/v1`），并在启动时打印一次“脱敏后的最终请求 URL”。

E. 最小预跑动作（建议）
1. 先补 OCR 重试（与 LLM 同级策略）。
2. 明确保留一份可审计且可提交的 run 摘要路径（或文档声明仅本地留痕）。
3. 用 Try-1（每套 1 人）实跑验证 token/API 路径配置，再放量执行 Try-2/Try-3。

decision: changes_requested
