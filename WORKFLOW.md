# ai_grading_manul 本地批改工作流

## 目标
- 对 `data/test1`、`data/test2` 的每个学生执行：
  1) OCR
  2) Judge（评分）
  3) Check（复核）
- 将每个学生的结果写入对应考试目录下的 `eval/`。

## 输入与输出

### 输入
- `data/<exam>/answer/*.pdf`：该套考试参考答案
- `data/<exam>/student/*.pdf`：学生答卷
- `.env`：
  - `TOKEN_OF_OCR`
  - `API_OF_QWEN`
  - `BASEURL`（示例：`https://openrouter.ai/api/v1`）
  - `MODELNAME`

### 输出
- `data/<exam>/eval/<student>/`
  - `题目与标答.md`
  - `作答md.md`
  - `评改结果.md`
  - `作答检查结果.md`
  - `检查后评改结果.md`
  - `judge_raw.json`
  - `check_raw.json`
  - `status.json`
- `data/<exam>/eval/_runs/<run_id>/`
  - `run_meta.json`
  - `events.jsonl`
- `reports/run_<run_id>.md` 与 `reports/run_<run_id>.json`（可提交留痕）

## 执行顺序（固定）
1. 先在 `develop` 分支开发，禁止直接在 `main` 实验。
2. 先小样本冒烟（每套考试 1 名学生）。
3. 再全量跑两套考试。
4. 再做一次重复执行验证缓存命中（幂等）。
5. 记录报告与失败清单，提交到 `develop`，确认后再合并 `main`。

## 尝试计划
- Try-1（冒烟）
  - `python3 scripts/run_batch_eval.py --exam test1 --max-students 1 --verbose`
  - `python3 scripts/run_batch_eval.py --exam test2 --max-students 1 --verbose`
- Try-2（全量）
  - `python3 scripts/run_batch_eval.py --exam test1 test2 --verbose`
- Try-3（幂等）
  - 再跑一次 Try-2，确认大部分阶段 `cache-hit`。

## 失败与重试策略
- 可重试：网络超时、429、5xx。
- 不重试：401/403、结构化参数错误（4xx）。
- 单学生失败不阻断整批；失败记录写入 `events.jsonl`。

## 注意
- 本工作流不改动线上 80 端口服务。
- 不在代码或日志中明文打印 `.env` 密钥。
- `data/` 目录在 `.gitignore` 中，运行产物默认仅本地保留；如需共享结果，用 `reports/` 的摘要文件。
