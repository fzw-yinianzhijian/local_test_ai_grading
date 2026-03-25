# ai_grading_manul

本仓库用于本地批量跑通：`OCR -> judge -> check`。

## 关键文件
- OCR 脚本：`mineru_pdf_extract.py`
- 批量执行脚本：`scripts/run_batch_eval.py`
- 工作流说明：`WORKFLOW.md`
- 路由/模型说明：`description.html`、`model_route_notes.md`

## 环境变量（.env）
- `TOKEN_OF_OCR`
- `API_OF_QWEN`
- `BASEURL`
- `MODELNAME`

## 常用命令
- 小样本冒烟：
  - `python3 scripts/run_batch_eval.py --exam test1 --max-students 1 --verbose`
- 全量执行两套考试：
  - `python3 scripts/run_batch_eval.py --exam test1 test2`
- 验证幂等缓存：
  - 再跑一次上面的全量命令，观察 `cache-hit` 事件
