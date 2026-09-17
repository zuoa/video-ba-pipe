# 单摄像头行人 ReID 跟踪

目标追踪节点的 `botsort_reid` 后端为行人提供会话级身份连续性，主要处理多人交叉、遮挡和同一机位内的离场再进入。它不生成跨摄像头全局身份，也不把外观特征写入数据库、日志或告警载荷。

## 模型契约与平台制品

一个 ReID 模型包代表一个固定特征空间，包含输入尺寸、预处理、embedding 维度、余弦距离和默认相似度阈值。不同平台制品必须由同一源模型导出或量化：

| 部署 | 推荐制品 |
| --- | --- |
| x86 CPU | ONNX Runtime CPU `.onnx` |
| x86 NVIDIA | ONNX Runtime CUDA / TensorRT EP `.onnx` |
| Jetson | TensorRT EP `.onnx` 或 TorchScript CUDA `.pt` |
| RK3588 | RKNNLite INT8 `.rknn` |

在“行人 ReID”管理页点击“添加模型”，填写名称并上传行人 ReID 特征模型文件。支持 `.onnx`、TorchScript `.pt`/`.pth` 和 `.rknn`，文件格式决定默认推理方式；普通目标检测模型以及未导出为 TorchScript 的 `.pth` 权重不能直接使用。模型契约编号由系统生成。页面默认按 256×128 输入、512 维输出和 ImageNet 标准化配置；若模型说明不同，在“高级设置”中填写实际输入尺寸、输出维度和预处理方式。上传后系统会在推理 Worker 中试运行，检查结果显示在模型卡片中。

也可以从 Hugging Face 下载；生产下载必须填写固定 revision 和 SHA-256，可通过 `HF_USE_MIRROR`、`HF_MIRROR_ENDPOINT` 使用国内镜像。精选目录由 `REID_MODEL_CATALOG_PATH` 指向的 JSON 数组提供，条目与自定义下载使用相同字段。同一模型包如需添加其他设备的文件，点击卡片上的“添加设备版本”，并确保所有文件由同一个源模型导出，输入、输出与预处理相同。

管理页会显示各模型包的平台匹配情况和后台下载状态。上传后会自动检查；也可以在卡片上点击“检查模型”重试，由推理 Worker 加载文件并校验 embedding 维度。只有检查通过才能确认模型实际可推理。Hugging Face 下载完成后需要手动点击“检查模型”。若页面显示 Worker 不可用，先检查 worker 服务及其推理依赖。模型包列表仍可管理，但无法试运行。

建议从 `OSNet-x0.25 / 256×128 / 512D` 开始做便携性验证，但第三方权重必须先完成许可证审核。ONNX 为参考输出；FP16 制品与参考 embedding 的余弦一致度应不低于 0.995，RKNN INT8 不低于 0.98，黄金集 Top-1 排名一致率不低于 99%。

## 工作流配置

1. 上游检测器输出 `person` 框。
2. 在目标追踪算法中选择 `BoT-SORT ReID` 和已配置的 ReID 模型包。
3. `reid_memory_seconds` 默认 300 秒，可在 5–3600 秒内调整；超过窗口后重新分配 `track_id`。
4. 固定机位保持相机运动补偿关闭。机架抖动或少量转动时可启用；镜头突变会清空运动状态，并保留仍在时间窗内的外观候选。

`track_id` 只在 `source_id + tracking_session_id` 内唯一。输出 attributes 包含 `reid_status`、`association_method`、`appearance_score`、`reidentified` 和 `reid_model_contract`，但不包含 embedding。

## 降级和容量

共享推理队列有界。模型不可用、队列过载或 worker 失败时，节点继续用运动和 IoU 输出本地轨迹，并设置 `reid_status=degraded` 与 `reid_degraded_reason`，不会沿用未经确认的旧身份。

容量验收基线为 8 路 1080p、每路 2 FPS、每帧最多 30 人。现场验证应同时报告 HOTA、IDF1、ID switches、重激活准确率和错误合并率；不能只以单张图片的 ReID 排名指标替代视频验收。
