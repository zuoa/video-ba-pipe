# 视频分析系统前端

基于 UmiJS + React + Ant Design + React Flow 构建

## 已完成功能

### 核心页面
- ✅ Dashboard - 实时数据统计、视频源状态、最近告警
- ✅ Workflows - React Flow 可视化编排、节点编辑、连线逻辑
- ✅ Algorithms - CRUD、测试功能、模板选择
- ✅ VideoSources - CRUD、流检测、快照预览、状态监控
- ✅ Alerts - 列表分页、详情查看、图片预览、视频播放
- ✅ Models - 上传下载、筛选搜索
- ✅ Scripts - Monaco 编辑器、分类管理

## 快速开始

```bash
# 安装依赖
cd frontend
npm install

# 启动开发服务器
npm run dev

# 访问 http://localhost:8000
```

## 后端配置

后端需安装 `flask-cors`:

```bash
pip install flask-cors
python -m app.web.webapp
```

## 技术栈

- UmiJS 4 - 企业级前端框架
- React 18 - UI 库
- Ant Design 5 - 组件库
- React Flow 11 - 流程图编排
- Monaco Editor - 代码编辑器
- TypeScript - 类型安全

## 目录结构

```
frontend/
├── src/
│   ├── pages/          # 页面组件
│   │   ├── dashboard/
│   │   ├── workflows/
│   │   ├── algorithms/
│   │   ├── video-sources/
│   │   ├── alerts/
│   │   ├── models/
│   │   └── scripts/
│   ├── services/       # API 服务
│   │   └── api.ts
│   └── global.css
├── .umirc.ts           # UmiJS 配置
└── package.json
```

## 构建生产版本

```bash
npm run build
# 产物在 dist/ 目录
```
