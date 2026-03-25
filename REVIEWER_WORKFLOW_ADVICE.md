A. 目标与约束确认
- 目标：批量跑两套考试 `data/test1`、`data/test2`，对每个考生执行 `OCR -> judge -> check`，结果落到各自 `eval` 目录。
- 约束：
  - 不改服务（仅本地脚本编排 + 外部 API 调用）。
  - OCR 与大模型凭证只从 `.env` 读取（已识别变量：`TOKEN_OF_OCR`、`API_OF_QWEN`、`BASEURL`、`MODELNAME`）。
  - 分支先 `develop` 再 `main`。
  - 全程留痕（命令、输入范围、输出摘要、失败清单）。

B. 最小可执行工作流（执行列表 + 尝试计划）
- 执行列表（最小 6 步）：
  1. 切分支并同步：`main pull --ff-only` -> `develop`。
  2. 准备运行环境：`python -m venv .venv && pip install -r requirements.txt`（建议补 `python-dotenv`）。
  3. 实现本地三阶段脚本（OCR/judge/check）与总控脚本（batch orchestrator）。
  4. 先做小样本试跑（每套考试 1 名学生，验证端到端）。
  5. 全量跑 `test1/test2`，启用 `--resume`，输出汇总报告。
  6. 复跑一次验证缓存命中与幂等（应显示大量 skip/cache-hit）。
- 尝试计划（建议顺序）：
  - Try-1（冒烟）：`test1` 1 人 + `test2` 1 人，确认 OCR/LLM 配置正确。
  - Try-2（全量）：两套全部学生，允许失败续跑，记录失败原因。
  - Try-3（幂等）：原参数再跑一遍，验证仅失败项重试/其余跳过。

C. 文件结构与脚本拆分（最小实现）
- 建议新增：
  - `scripts/run_batch.py`：总控；遍历 `test1,test2`，逐学生调三阶段；写总报告。
  - `scripts/stage_ocr.py`：封装 OCR 调用（基于 `TOKEN_OF_OCR`）。
  - `scripts/stage_judge.py`：封装 judge（`BASEURL` + `API_OF_QWEN` + `MODELNAME`）。
  - `scripts/stage_check.py`：封装 check（同上，输入 OCR+judge 输出）。
  - `scripts/common/io_utils.py`：原子写文件、路径规范化。
  - `scripts/common/retry_utils.py`：统一重试策略。
  - `scripts/common/hash_utils.py`：输入哈希、cache key。
- 每套考试输出结构（写到各自 `eval`）：
  - `data/testX/eval/_runs/<run_id>/run_meta.json`
  - `data/testX/eval/_runs/<run_id>/events.jsonl`
  - `data/testX/eval/<student_id>/ocr.json`
  - `data/testX/eval/<student_id>/judge.json`
  - `data/testX/eval/<student_id>/check.json`
  - `data/testX/eval/<student_id>/final.json`
  - `data/testX/eval/<student_id>/status.json`（阶段状态机）

D. 幂等、缓存、失败重试、留痕策略
- 幂等与缓存：
  - 以 `input_hash = sha256(answer_pdf + student_pdf + prompt_version + model + stage_code_version)` 作为阶段键。
  - 每阶段成功后写 `status.json`：`{"stage":"ocr|judge|check","input_hash":"...","ok":true,"ts":"..."}`。
  - 下次运行若 `ok=true` 且 `input_hash` 未变，则直接 `cache-hit` 跳过。
  - 输出采用“临时文件 + 原子 rename”，避免中途中断导致坏文件。
- 失败重试：
  - 可重试错误：超时、429、5xx、连接重置；指数退避（2s/4s/8s/16s/32s，最多 5 次，含抖动）。
  - 不重试错误：鉴权失败（401/403）、参数错误（大多 4xx）、响应结构校验失败。
  - 单学生失败不终止全局；最终给出失败清单，支持 `--only-failed` 重跑。
- 留痕：
  - 每次运行生成唯一 `run_id`，记录参数快照（考试集、模型名、并发度、是否 resume）。
  - `events.jsonl` 逐条记录：`exam/test/student/stage/start/end/duration/retry_count/result/error`。
  - 仓库内提交一份摘要：`reports/run_<yyyymmdd_hhmm>.md`（不含密钥、不含原始答卷内容）。

E. develop -> main 的最小交付门禁
- 分支流程：
  1. `git checkout main && git pull --ff-only`
  2. `git checkout -b develop`（或已有 `develop` 则 `git checkout develop && git pull --ff-only`）
  3. 开发 + 三次尝试（Try-1/2/3）+ 记录证据。
  4. 提交内容建议仅含：脚本、文档、报告模板（`data/` 当前被忽略，不提交运行产物）。
  5. 在 `develop` 自检通过后再合并到 `main`。
- 最小通过标准：
  - 两套考试都产出 `eval/<student>/final.json`（允许少量失败，但必须有失败清单与重试记录）。
  - 复跑有明确 cache-hit（证明幂等可用）。
  - 有一份可审计的运行摘要文档（命令、时间、结果统计）。

decision: approve_with_execution_plan
