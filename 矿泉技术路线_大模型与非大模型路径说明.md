# MinerU 技术路线说明：大模型与非大模型处理路径对比

本文说明 MinerU 在 `pipeline` 与 `vlm/hybrid` 两条主路径下的处理方式差异，帮助快速判断何时走纯模型流水线（非大模型路线）、何时走视觉语言大模型（VLM）路线。

## 一、入口与路由决策

CLI 入口会把 `backend` 转交给不同处理器：

- `pipeline` -> 走 ` _process_pipeline`
- `vlm-*` -> 走 `_process_vlm`
- `hybrid-*` -> 走 `_process_hybrid`
- `*-auto-engine` 会先经过 `get_vlm_engine()` 自动选引擎（`transformers` / `vllm` / `lmdeploy` / `mlx`）

参考：
- [cli/client.py - backend 选项定义](/Users/fengzw/tsgit/MinerU/mineru/cli/client.py#L54)
- [cli/common.py - do_parse 分发](/Users/fengzw/tsgit/MinerU/mineru/cli/common.py#L414)

---

## 二、`pipeline` 路线：不是单一大模型，是“多模型传统解析流水线”

### 特点
- 不需要 VLM 大模型，属于“检测 + OCR + 表格/公式专模”的组合。
- 支持纯 CPU（虽然速度/精度配置会受设备影响）。
- 更像“CV/NLP 组件拼装”的文档版面解析。

### 具体步骤
1. **输入预处理**
   - `pdf_bytes` 按页转为图像
   - `parse_method=auto/ocr/txt` 决定是否启用 OCR
   - OCR 判定走 `classify(pdf_bytes)`（可选）
   - 入口：
     - [pipeline_doc_analyze()](/Users/fengzw/tsgit/MinerU/mineru/backend/pipeline/pipeline_analyze.py#L70)

2. **布局检测与对象检测**
   - `layout` 主干（DocLayout YOLO）输出文本块/图片/表格/公式候选区
   - 可能触发公式检测（MFD）与公式识别（MFR）
   - 表格分支会走有线/无线两套表格模型链路
   - 处理函数：
     - [BatchAnalyze.__call__()](/Users/fengzw/tsgit/MinerU/mineru/backend/pipeline/batch_analyze.py#L25)
     - [layout 识别](/Users/fengzw/tsgit/MinerU/mineru/backend/pipeline/batch_analyze.py#L50)
     - [公式检测与识别](/Users/fengzw/tsgit/MinerU/mineru/backend/pipeline/batch_analyze.py#L56)

3. **OCR检测 + 文字识别**
   - 对“非文本提取块”先检测文本区域，再做文字识别（rec）
   - 不同语言可复用同一 OCR 引擎实例（按语言分组批量）
   - 入口：
     - [OCR-det](/Users/fengzw/tsgit/MinerU/mineru/backend/pipeline/batch_analyze.py#L238)
     - [OCR-rec](/Users/fengzw/tsgit/MinerU/mineru/backend/pipeline/batch_analyze.py#L366)

4. **输出组装**
   - `pipeline_result_to_middle_json(...)` 把模型输出转成中间格式
   - 后续交给 `pipeline_union_make` 产出 markdown、content_list、中间 JSON
   - 入口：
     - [pipeline_result_to_middle_json()](/Users/fengzw/tsgit/MinerU/mineru/backend/pipeline/model_json_to_middle_json.py)
     - [输出路径](/Users/fengzw/tsgit/MinerU/mineru/cli/common.py#L210)

### 组件来源（简化）
- Layout：`doclayout_yolo`
- OCR：`PytorchPaddleOCR`
- 公式检测/识别：`YOLOv8MFD` + `Unimernet / FormulaRecognizer`
- 表格：`RapidTable` + `UnetTable`
- 统一入口模型组装：`MineruPipelineModel` / `ModelInit`

参考：
- [model_init.py](/Users/fengzw/tsgit/MinerU/mineru/backend/pipeline/model_init.py#L18)

---

## 三、`vlm` 路线：视觉语言大模型（VLM）路线

### 特点
- 使用单一大模型做文档语义+视觉联合推理（两阶段提取）。
- 使用默认模型仓库为 `opendatalab/MinerU2.5-2509-1.2B`
- 与 `vllm/lmdeploy/transformers/mlx` 是运行时引擎类型，不等价于 `vlm` 本身。

### 具体步骤
1. **模型选择与加载**
   - `backend` 形如 `vlm-transformers/vlm-vllm-engine/vlm-auto-engine`；
   - `*-auto-engine` 会调用 `get_vlm_engine()` 自动判断
   - 若未传 `model_path`，会通过 `auto_download_and_get_model_root_path("/", "vlm")` 拉取模型
   - 加载时 `Qwen2VLForConditionalGeneration.from_pretrained(...)`（transformers 后端）
   - 参考：
     - [vlm get_model](/Users/fengzw/tsgit/MinerU/mineru/backend/vlm/vlm_analyze.py#L32)
     - [模型下载映射](/Users/fengzw/tsgit/MinerU/mineru/utils/models_download_utils.py#L1)
     - [VLM ID](/Users/fengzw/tsgit/MinerU/mineru/utils/enum_class.py#L93)

2. **页面转图片**
   - `load_images_from_pdf` 输出 `PIL` 图片列表
   - 参考：
     - [vlm doc_analyze](/Users/fengzw/tsgit/MinerU/mineru/backend/vlm/vlm_analyze.py#L234)

3. **VLM 推理**
   - `predictor.batch_two_step_extract(images=...)`
   - 每页返回结构化块（文本、图片、表格、公式等）
   - 参考：
     - [vllm/transformers 统一调用](/Users/fengzw/tsgit/MinerU/mineru/backend/vlm/vlm_analyze.py#L240)

4. **后处理**
   - 通过 `result_to_middle_json(...)` 转换为标准中间格式
   - 输出阶段与 `pipeline` 一样生成 markdown/content_list/middle_json，但使用 VLM 的后处理函数
   - 参考：
     - [VLM 解析到中间结构](/Users/fengzw/tsgit/MinerU/mineru/backend/vlm/vlm_analyze.py#L245)
     - [输出分支](/Users/fengzw/tsgit/MinerU/mineru/cli/common.py#L300)

---

## 四、`hybrid` 路线：VLM + 局部 Pipeline 增强

### 核心逻辑
- 默认先跑 VLM；在需要时对某些区域补 OCR/公式（尤其文本回填）
- 当 `ocr_enable` + 语言为 `ch/en` + 行内公式开关开启时，可启用 VLM OCR；可通过环境变量强制切换（如 `MINERU_FORCE_VLM_OCR_ENABLE`）
- 参考：
  - [hybrid 决策](/Users/fengzw/tsgit/MinerU/mineru/backend/hybrid/hybrid_analyze.py#L369)
  - [hybrid 主流程](/Users/fengzw/tsgit/MinerU/mineru/backend/hybrid/hybrid_analyze.py#L384)

### 结果合并
- 先得到 `results`（VLM结构）
- 再按策略补充 `inline_formula_list`、`ocr_res_list`，归一化坐标
- 输出中间结果与 markdown

---

## 五、是否“用大模型”与“可不可以不调用大模型”

- **是**，`pipeline` 本身不必调用 VLM 大模型；它是多组件传统模型流水线。
- **是**，`vlm` 代表的是 VLM 大模型路径，通常更高精度但对算力更敏感。
- **是**，两条路都依赖模型，但“模型规模”和“推理范式”不同：
  - `pipeline`：多个较小/中等模型做分工
  - `vlm`：统一的视觉语言大模型做文档语义建模

---

## 六、建议（实际落地）

- 资源受限/需稳定 CPU 运行：优先 `pipeline`
- 追求高精度/复杂版面，且有足够 GPU：优先 `vlm-auto-engine` 或 `hybrid-auto-engine`
- 有 OpenAI 兼容服务时：可用 `*-http-client`

对应配置入口可先看：
- [backend 说明](/Users/fengzw/tsgit/MinerU/README.md)
- [后端行为映射](/Users/fengzw/tsgit/MinerU/mineru/cli/client.py#L61)
