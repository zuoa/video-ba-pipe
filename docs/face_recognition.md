# 跨平台 1:N 人脸识别

本实现面向约 1,000 人的白名单识别。检测、五点对齐、特征提取属于平台推理层；人员、人脸库、阈值、多帧确认、事件和审计属于统一业务层。相同 `contract_id` 的不同平台制品必须产生同维度、同归一化空间的特征，人员模板因此不需要随部署架构重复录入。

> 当前版本不做活体检测，所有事件明确标记 `liveness_status=not_checked`。它不能单独用于支付、门禁放行等高风险自动决策。

## 支持矩阵

| 部署 | 首选运行时 | 制品 | 建议 `architecture` / `device` |
| --- | --- | --- | --- |
| x86 CPU | ONNX Runtime CPU | `.onnx` | `amd64` / `cpu` |
| x86 NVIDIA | TorchScript CUDA；安装 ORT GPU 后可用 ONNX CUDA | `.pt` 或 `.onnx` | `amd64` / `cuda` |
| Jetson Orin NX | TorchScript CUDA；安装 TensorRT EP 后可用 TensorRT | `.pt` 或 `.onnx` | `arm64` / `jetson` |
| RK3588 | RKNNLite 2.3.2 | `.rknn` | `arm64` / `rk3588` |

`auto` 只会选择同一运行时下完整的检测模型和特征模型，不会混用未经验证的半套制品。如果首选运行时缺失，会依次尝试当前设备可用的 GPU 运行时和 ONNX CPU。可在“系统设置 > 人脸识别”中固定全局默认运行时，用于验收或故障隔离；工作流仍可按需覆盖。

新增加速器无需改动名单、检索或工作流代码：可信模块调用 `app.core.face_inference.register_face_runner(runtime, factory, extensions=...)` 注册 runner，在 `FACE_INFERENCE_PLUGIN_MODULES` 中声明模块，再从“系统设置 > 人脸识别”选择对应 runtime。插件加载错误会出现在 `/api/face/runtime` 的能力信息中。

TensorRT 制品当前通过 ONNX Runtime TensorRT Execution Provider 执行，因此上传 `.onnx`，不接收绑定 GPU 型号和 TensorRT 版本的 `.engine`。

## 上线步骤

1. 生成生物数据密钥，并保证 API、worker 和 source host 读取同一密钥。推荐把密钥写入权限为 `0400` 的持久文件，并设置 `FACE_DATA_ENCRYPTION_KEY_FILE=/data/secrets/face-data.key`。

   ```bash
   mkdir -p data/secrets
   openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n=' > data/secrets/face-data.key
   chmod 0400 data/secrets/face-data.key
   ```

2. 执行数据库初始化，随后启动对应架构的 Compose。

   ```bash
   docker compose -f docker-compose.yml up -d
   # 或 docker-compose.yml.x86+cuda / .jetson / .rknn
   ```

3. 启动后在“系统设置 > 人脸识别”管理事件保留期、默认推理后端和商用模型门禁。这些运行策略存储在 `SystemSetting` 中，由 API、worker 和 source host 共享；数据目录、加密密钥和可信插件模块仍是启动级配置。

4. 以管理员进入“人脸识别 → 跨平台模型”，创建逻辑模型包。先记录权重来源、许可证、特征维度和稳定的 `contract_id`，再为目标平台分别上传 `detection` 与 `embedding` 制品。

5. 创建人脸库并绑定模型包。单人录入建议 3–5 张清晰正脸，覆盖现场常见角度和光照。也可使用批量 ZIP。

6. 在脚本算法库中选择“跨平台人脸识别”，配置 `gallery_id` 后加入工作流。实时节点默认使用已有的共享推理进程、有限队列和共享内存传帧。

7. 使用独立的现场验证集校准阈值。管理页的“阈值离线评估”只根据库内同人/异人模板给出初始建议，不替代现场 FPIR/FNIR 验收。

## 批量录入格式

ZIP 不得包含绝对路径、`..` 或重复文件名。总上传大小限制 512 MB，解压后限制 2 GB；每人在同一模型契约下最多保留 5 张模板照片。

```text
people.zip
├── manifest.csv
└── photos/
    ├── E-1001/
    │   ├── front.jpg
    │   └── indoor.jpg
    └── E-1002/
        └── front.png
```

`manifest.csv`：

```csv
person_code,name
E-1001,张三
E-1002,李四
```

管理端先做结构预检，再提交后台任务。应用的持久暂存只写入 AES-256-GCM 流式密文，处理完成后删除；每张原图和特征向量也分别认证加密。反向代理和系统临时目录仍应按敏感数据要求加固并设置短生命周期。

## 识别策略

- 人脸检测后先检查最小人脸尺寸、曝光和清晰度；不合格帧不判定为陌生人。
- 512 维特征做 L2 归一化。千人级库采用 NumPy 精确余弦检索，单人多个模板取最高分。
- 分数高于高阈值可直接确认为白名单；低、高阈值之间默认需要 3 帧内 2 次命中同一人；连续 3 个合格非匹配才确认为陌生人。
- 同一跟踪轨迹只落一条确认事件，抑制重复抓拍。人脸库版本变化会使内存索引失效并自动重载。
- 更换特征模型契约后，旧模板不会进入新库索引。若新旧模型特征空间不兼容，必须重新生成模板。

## 模型输出契约

检测制品支持一个 `N×15` 输出：`x1,y1,x2,y2,score` 加 5 个关键点；也支持通过制品 `metadata.output_indexes` 指定独立的 boxes、scores、landmarks 输出。常用元数据示例：

```json
{
  "input_shape": "320x320",
  "input_layout": "nchw",
  "input_dtype": "float32",
  "color": "rgb",
  "mean": [127.5, 127.5, 127.5],
  "std": [128, 128, 128],
  "output_format": "combined",
  "coordinates_are_absolute": true
}
```

特征制品输入为对齐后的 `112×112` 人脸，输出一行特征。业务层会再次 L2 归一化并验证维度。未声明批处理能力时按固定 `batch=1` 执行，以兼容常见 RKNN/边缘 ONNX 制品；固定批大小可声明 `metadata.batch_size`（不足一批时安全填充），动态批处理可声明 `metadata.dynamic_batch=true`，或用 `metadata.max_batch_size` 限制单批上限。

工作流传入 ROI 时，人脸框中心必须落在任一 ROI 内才会进入跟踪、身份检索和事件持久化；上游 `roi_draw` 仍优先于算法节点和算法默认 ROI。

## 性能验收

目标负载是 8 路 1080p、每路分析 2 FPS。RK3588 和 Jetson 的目标是端到端 P95 小于 1 秒；x86 CPU 根据核数分级验收，不承诺与 NPU/GPU 同档。测试时至少记录：解码延迟、共享推理排队时间、检测/特征耗时、总 P50/P95/P99、丢帧数、设备利用率和温度降频。

1,000 人、每人 5 个 512 维 FP32 模板约占 10 MB 纯向量内存，精确搜索通常不是瓶颈；检测和特征推理才是容量规划重点。超过约 10 万模板后再考虑 HNSW/FAISS，并保留精确重排。

## 安全与运维

- 原图、特征、事件抓拍和导入暂存包均加密落盘；数据库只存密文和必要索引字段。
- 已识别与陌生人事件及抓拍默认分别保留 90/30 天，在“系统设置 > 人脸识别”中调整；设为 0 可禁止持久化该类新事件。
- 密钥丢失后历史生物数据不可恢复。生产环境必须做受控备份、轮换方案和最小权限管理，不能把真实密钥提交到仓库。
- API 只有管理员可写模型、名单和模板；普通用户只能读取自己名下的数据。
- 模型包会保存制品 SHA-256 和许可证信息。仓库不附带生产权重；内部验证模型在商用前必须替换为权利清晰、经过目标人群偏差评估的模型。生产环境建议在系统设置中开启“只允许已声明可商用的模型”，硬性拒绝未声明可商用的模型包。

## API 入口

主要接口位于 `/api/face`：`model-bundles`、`galleries`、`persons`、`imports`、`calibrations`、`events`。运行策略使用 `/api/system/face-recognition-config`。所有接口需要现有 JWT，写操作需要管理员权限。
