# AI 数学试卷自动批改系统

本仓库实现了一套完整的本地批量 **OCR → 评分(Judge) → 复核(Check)** 流水线，用于数学考试试卷的自动批阅。

## 工作流总览

```
PDF 答案 ──OCR──▶ answer/full.md（仅首次执行，后续复用）
                       │
PDF 学生答卷 ──OCR──▶ eval/_cache/ocr/<hash>/full.md（按文件哈希缓存）
                       │
                       ▼
              ┌────────────────┐
              │  Stage 1: OCR  │  MinerU 精准解析 API（VLM 模型）
              └───────┬────────┘
                      ▼
              ┌────────────────┐
              │ Stage 2: Judge │  LLM 逐题评分（行级说明 + 思路梳理 + 打分表）
              └───────┬────────┘
                      ▼
              ┌────────────────┐
              │ Stage 3: Check │  LLM 复核（检查提取错误、修正评分）
              └───────┬────────┘
                      ▼
           eval/<run_id>/<学生>/
             ├── 题目与标答.md
             ├── 作答md.md
             ├── 评改结果.md
             ├── 作答检查结果.md
             ├── 检查后评改结果.md   ← 最终结果
             ├── judge_raw.json
             ├── check_raw.json
             └── status.json
```

## 目录结构

```
ai_grading_manul/
├── .env                          # 环境变量（不提交）
├── .gitignore
├── README.md                     # 本文件
├── WORKFLOW.md                   # 工作流详细说明
├── mineru_pdf_extract.py         # MinerU OCR 封装脚本
├── scripts/
│   └── run_batch_eval.py         # 批量评测主脚本
├── data/
│   ├── test1/                    # 考试 1
│   │   ├── answer/               # 参考答案
│   │   │   ├── *.pdf             # 答案 PDF（每场考试一个）
│   │   │   ├── full.md           # 答案 OCR 结果（自动生成，仅首次）
│   │   │   └── ocr_meta.json     # OCR 元信息
│   │   ├── student/              # 学生答卷
│   │   │   ├── 张三.pdf
│   │   │   ├── 李四.pdf
│   │   │   └── ...
│   │   └── eval/                 # 评测结果（按运行时间戳分组）
│   │       ├── _cache/           # OCR 缓存（跨运行共享）
│   │       │   └── ocr/<sha256>/full.md
│   │       ├── 20260326_143000/  # 第一次运行
│   │       │   ├── run_meta.json
│   │       │   ├── events.jsonl
│   │       │   ├── 张三/
│   │       │   │   ├── 题目与标答.md
│   │       │   │   ├── 作答md.md
│   │       │   │   ├── 评改结果.md
│   │       │   │   ├── 作答检查结果.md
│   │       │   │   ├── 检查后评改结果.md
│   │       │   │   ├── judge_raw.json
│   │       │   │   ├── check_raw.json
│   │       │   │   └── status.json
│   │       │   └── 李四/
│   │       │       └── ...
│   │       └── 20260327_090000/  # 第二次运行（可对比）
│   │           └── ...
│   └── test2/                    # 考试 2（结构同上）
└── reports/                      # 全局运行报告
    ├── run_20260326_143000.json
    └── run_20260326_143000.md
```

## 快速开始

### 1. 环境准备

```bash
# Python 3.10+
pip install requests

# 在仓库根目录创建 .env
cat > .env << 'EOF'
TOKEN_OF_OCR=你的MinerU_API_Token
API_OF_QWEN=你的LLM_API_Key
BASEURL=https://openrouter.ai/api/v1
MODELNAME=qwen/qwen3-235b-a22b
EOF
```

### 2. 准备数据

将考试数据按如下结构放入 `data/` 目录：

```
data/<考试名>/
├── answer/
│   └── 参考答案.pdf      # 每场考试恰好一个 PDF
└── student/
    ├── 学生A.pdf          # 文件名即为学生姓名
    ├── 学生B.pdf
    └── ...
```

### 3. 运行批改

```bash
# 切换到仓库根目录
cd ai_grading_manul

# 冒烟测试：每场考试只跑 1 名学生
python3 scripts/run_batch_eval.py --exam test1 --max-students 1 --verbose

# 全量运行所有考试
python3 scripts/run_batch_eval.py --exam test1 test2 --verbose

# 不指定 --exam 则自动扫描 data/ 下的所有子目录
python3 scripts/run_batch_eval.py --verbose
```

### 4. 查看结果

每次运行会生成一个时间戳目录 `eval/<YYYYMMDD_HHMMSS>/`，可以对比不同运行的评测结果：

```bash
# 查看某次运行的总报告
cat reports/run_20260326_143000.md

# 查看某学生的最终批改结果
cat data/test1/eval/20260326_143000/张三/检查后评改结果.md

# 列出所有运行
ls data/test1/eval/
```

## 命令行参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--repo-root` | `.` | 仓库根目录路径 |
| `--data-dir` | `data` | 数据目录（相对于 repo-root） |
| `--exam` | 全部 | 指定要处理的考试文件夹，如 `test1 test2` |
| `--max-students` | 无限制 | 每场考试最多处理的学生数（用于测试） |
| `--student-regex` | 无 | 按正则过滤学生文件名，如 `"张.*"` |
| `--force` | `false` | 强制重新运行所有阶段（忽略缓存） |
| `--sleep` | `0.5` | 学生之间的等待秒数（避免 API 限流） |
| `--verbose` | `false` | 输出详细日志（含每个事件的 JSON） |

## 缓存机制

系统实现了多级缓存以避免重复计算：

| 缓存层 | 位置 | 键 | 跨运行共享 |
|---|---|---|---|
| 答案 OCR | `answer/full.md` | 文件是否存在 | 是 |
| 学生 OCR | `eval/_cache/ocr/<sha256>/full.md` | PDF 文件 SHA-256 | 是 |
| Judge 评分 | 当前 `eval/<run_id>/<学生>/status.json` | 答案+作答+模型 的哈希 | 否（每次运行独立） |
| Check 复核 | 当前 `eval/<run_id>/<学生>/status.json` | 答案+作答+评分+模型 的哈希 | 否（每次运行独立） |

- **OCR 缓存跨运行共享**：同一份 PDF 不会被重复 OCR，无论运行多少次评测。
- **Judge/Check 每次运行独立**：每个时间戳目录是一次完整的评测，方便换模型或调 prompt 后重新评测对比。
- 使用 `--force` 可强制忽略所有缓存重新执行。

## 环境变量

在仓库根目录的 `.env` 文件中配置（或通过系统环境变量设置）：

| 变量名 | 说明 | 示例 |
|---|---|---|
| `TOKEN_OF_OCR` | MinerU 精准解析 API 的 JWT Token | `eyJhbGci...` |
| `API_OF_QWEN` | LLM API Key（OpenAI 兼容格式） | `sk-...` |
| `BASEURL` | LLM API Base URL | `https://openrouter.ai/api/v1` |
| `MODELNAME` | 使用的模型名称 | `qwen/qwen3-235b-a22b` |

## 输出文件说明

每个学生目录下的文件：

| 文件 | 说明 |
|---|---|
| `题目与标答.md` | 参考答案的 OCR Markdown（方便对照） |
| `作答md.md` | 学生答卷的 OCR Markdown |
| `评改结果.md` | Judge 阶段的逐题评分结果 |
| `作答检查结果.md` | Check 阶段发现的问题（提取错误、评分偏差等） |
| `检查后评改结果.md` | **最终评分结果**（经过复核修正） |
| `judge_raw.json` | Judge 阶段 LLM API 的原始响应 |
| `check_raw.json` | Check 阶段 LLM API 的原始响应 |
| `status.json` | 各阶段执行状态与缓存哈希 |

运行级别文件：

| 文件 | 说明 |
|---|---|
| `eval/<run_id>/run_meta.json` | 运行参数（模型、时间、选项等） |
| `eval/<run_id>/events.jsonl` | 详细事件日志（每个阶段的开始/完成/重试/错误） |
| `reports/run_<run_id>.json` | 全局运行报告（JSON 格式） |
| `reports/run_<run_id>.md` | 全局运行报告（Markdown 摘要表格） |

## 重试与容错

- **OCR 失败**：最多重试 4 次，指数退避（2^i 秒 + 随机抖动）。
- **LLM 调用失败**：最多重试 5 次；对 429/5xx 自动重试，4xx（非 429）立即报错。
- **单个学生失败不阻断**：失败记录写入 `events.jsonl` 并汇总到报告，其他学生继续执行。

## 多次评测对比

每次运行自动创建带时间戳的 `eval/<run_id>/` 目录，可以：

1. **换模型重评**：修改 `.env` 中的 `MODELNAME`，再次运行
2. **调整 prompt 重评**：修改代码中的 prompt 模板，再次运行
3. **对比结果**：不同 `run_id` 目录下的同名学生文件可直接 diff

```bash
# 对比两次运行中同一学生的最终评分
diff data/test1/eval/20260326_143000/张三/检查后评改结果.md \
     data/test1/eval/20260327_090000/张三/检查后评改结果.md
```

## 安全注意事项

- `.env` 文件已在 `.gitignore` 中，不会被提交到仓库。
- `data/` 目录也在 `.gitignore` 中，运行产物仅本地保留。
- 脚本不会在日志中打印 API Key 等敏感信息。
- 如需共享评测结果，使用 `reports/` 下的摘要文件。
