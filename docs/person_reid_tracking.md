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

在“行人 ReID”管理页创建逻辑模型包，再上传各平台制品。也可以从 Hugging Face 下载；生产下载必须填写固定 revision 和 SHA-256，可通过 `HF_USE_MIRROR`、`HF_MIRROR_ENDPOINT` 使用国内镜像。精选目录由 `REID_MODEL_CATALOG_PATH` 指向的 JSON 数组提供，条目与自定义下载使用相同字段。

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
